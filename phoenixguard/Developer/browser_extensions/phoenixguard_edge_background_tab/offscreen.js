import {
  SAMPLE_FPS,
  SAMPLE_INTERVAL_MS,
  SELECTION_TIMEOUT_MS,
  SOURCE_FREEZE_TIMEOUT_MS,
  canonicalFrameSignaturePayload,
  frameIngestConfigEndpoint,
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
  sourceOrigin
} from "./common.js";

const videoHost = document.getElementById("captureVideos");
const frameCanvas = document.getElementById("frameCanvas");
const probeCanvas = document.getElementById("probeCanvas");
const frameContext = frameCanvas.getContext("2d", {alpha: false});
const probeContext = probeCanvas.getContext("2d", {alpha: false, willReadFrequently: true});

let activeSession = null;
let candidateSession = null;
let candidateTimer = null;
let sampleTimer = null;
let uploadTimer = null;
let contractTimer = null;
let uploadInFlight = false;
let samplingPausedForSelector = false;
let pendingFrame = null;
let previousQueuedProbe = null;
let lastQueuedAt = 0;
let lastAcceptedAtMs = 0;
let lastVisualChangeAtMs = 0;
let serverArmed = false;
let signatureRequired = false;
let status = initialStatus();

function clearTimer(handle) {
  if (handle) clearTimeout(handle);
  return null;
}

function errorText(value) {
  if (value instanceof Error) return value.message;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function currentVideoAge(session = activeSession) {
  refreshMediaPlayback(session);
  if (!session?.lastVideoFrameAtMs) return -1;
  return Math.max(0, Date.now() - session.lastVideoFrameAtMs);
}

function refreshMediaPlayback(session, nowMs = Date.now()) {
  if (!session || session.stopped || !session.video) return false;
  let decodedFrames = -1;
  try {
    decodedFrames = Number(session.video.getVideoPlaybackQuality?.().totalVideoFrames ?? -1);
  } catch {
    decodedFrames = -1;
  }
  const progress = mediaPlaybackAdvanced(
    {mediaTime: session.observedMediaTime, decodedFrames: session.observedDecodedFrames},
    {mediaTime: Number(session.video.currentTime), decodedFrames}
  );
  session.observedMediaTime = progress.mediaTime;
  session.observedDecodedFrames = progress.decodedFrames;
  if (progress.advanced) {
    session.mediaTime = Math.max(0, progress.mediaTime);
    session.presentedFrames = Math.max(session.presentedFrames, progress.decodedFrames, 0);
    session.lastVideoFrameAtMs = nowMs;
  }
  return progress.advanced;
}

function activeFresh() {
  const age = currentVideoAge();
  const track = activeSession?.stream?.getVideoTracks?.()[0] || null;
  return Boolean(
    activeSession &&
    track?.readyState === "live" &&
    track.muted !== true &&
    age >= 0 && age <= SOURCE_FREEZE_TIMEOUT_MS
  );
}

function statusIdentity(update = {}) {
  const candidate = candidateSession;
  const active = activeSession;
  return {
    lockedTabId: active?.tab.id || Number(update.lockedTabId || 0),
    lockedTitle: active?.tab.title || String(update.lockedTitle || ""),
    lockedUrl: active?.tab.url || String(update.lockedUrl || ""),
    lockedOrigin: active?.tab.origin || String(update.lockedOrigin || ""),
    sequenceId: active?.sequenceId || String(update.sequenceId || ""),
    region: active?.region || update.region || null,
    candidateTabId: candidate?.tab.id || 0,
    candidateTitle: candidate?.tab.title || "",
    candidateUrl: candidate?.tab.url || "",
    candidateOrigin: candidate?.tab.origin || "",
    selectionId: candidate?.selectionId || "",
    sourceGeneration: active?.sourceGeneration || 0,
    sourceLeaseActive: Boolean(active?.sourceLeaseId)
  };
}

async function notifyStatus(update = {}) {
  const effectiveUpdate = {...update};
  if (candidateSession && !["error", "configuration_required"].includes(String(effectiveUpdate.phase || ""))) {
    effectiveUpdate.phase = "selecting";
    effectiveUpdate.message = "Select the exact chart rectangle. Candidate pixels are not being uploaded.";
  }
  const videoAge = currentVideoAge();
  status = initialStatus({
    ...status,
    ...statusIdentity(effectiveUpdate),
    ...effectiveUpdate,
    transportFrameAgeMs: videoAge,
    visualChangeAgeMs: lastVisualChangeAtMs > 0 ? Math.max(0, Date.now() - lastVisualChangeAtMs) : -1,
    sourceRenderFresh: activeFresh(),
    lastVideoFrameAt: activeSession?.lastVideoFrameAtMs
      ? new Date(activeSession.lastVideoFrameAtMs).toISOString()
      : "",
    lastVisualChangeAt: lastVisualChangeAtMs > 0 ? new Date(lastVisualChangeAtMs).toISOString() : "",
    updatedAt: new Date().toISOString()
  });
  try {
    await chrome.runtime.sendMessage({type: "OFFSCREEN_STATUS_V2", status});
  } catch (error) {
    console.debug("Status delivery deferred until the service worker wakes.", error);
  }
  return status;
}

function canvasBlob(canvas, type, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("Edge could not encode the selected chart region."));
    }, type, quality);
  });
}

function byteArrayToHex(bytes) {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(blob) {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return byteArrayToHex(new Uint8Array(digest));
}

async function hmacSha256Hex(secret, payload) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    {name: "HMAC", hash: "SHA-256"},
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(payload));
  return byteArrayToHex(new Uint8Array(signature));
}

function createVideo() {
  const video = document.createElement("video");
  video.autoplay = true;
  video.muted = true;
  video.playsInline = true;
  videoHost.appendChild(video);
  return video;
}

function watchPresentedFrames(session) {
  if (!session || session.stopped || typeof session.video.requestVideoFrameCallback !== "function") return;
  session.frameCallbackId = session.video.requestVideoFrameCallback((_now, metadata) => {
    if (session.stopped) return;
    session.presentedFrames = Number(metadata?.presentedFrames || session.presentedFrames + 1);
    session.mediaTime = Number(metadata?.mediaTime || 0);
    session.observedMediaTime = Math.max(session.observedMediaTime, session.mediaTime);
    session.observedDecodedFrames = Math.max(session.observedDecodedFrames, session.presentedFrames);
    session.lastVideoFrameAtMs = Date.now();
    if (session === activeSession && status.phase === "source_frozen") {
      void notifyStatus({
        phase: serverArmed && status.acceptedFrames > 0 ? "live" : "starting",
        message: "The selected chart region resumed rendering."
      });
    }
    watchPresentedFrames(session);
  });
}

async function waitForVideo(session) {
  const video = session.video;
  if (video.readyState >= HTMLMediaElement.HAVE_METADATA && video.videoWidth > 0) return;
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("Timed out waiting for chart-tab pixels.")), 8_000);
    video.addEventListener("loadedmetadata", () => {
      clearTimeout(timeout);
      resolve();
    }, {once: true});
  });
}

function waitForCleanVideoFrame(session, timeoutMs = 1_500) {
  if (typeof session?.video.requestVideoFrameCallback !== "function") {
    return new Promise((resolve) => setTimeout(resolve, 120));
  }
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        resolve();
      }
    }, timeoutMs);
    session.video.requestVideoFrameCallback(() => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve();
    });
  });
}

async function waitForUploadIdle(timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (uploadInFlight && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  if (uploadInFlight) throw new Error("Timed out waiting for the previous source upload to settle.");
}

async function createCapturedSession(message) {
  const video = createVideo();
  const session = {
    stream: null,
    video,
    tab: {
      id: Number(message.tab?.id || 0),
      title: String(message.tab?.title || "").slice(0, 180),
      url: sanitizeSourceUrl(message.tab?.url).slice(0, 2048),
      origin: sourceOrigin(message.tab?.url)
    },
    selectionId: String(message.selectionId || ""),
    sequenceId: String(message.sequenceId || ""),
    region: null,
    config: normalizeConfig(message.config),
    stopped: false,
    presentedFrames: 0,
    mediaTime: 0,
    observedMediaTime: -1,
    observedDecodedFrames: -1,
    lastVideoFrameAtMs: 0,
    frameCallbackId: 0,
    reusesActive: false,
    sourceGeneration: 0,
    sourceLeaseId: ""
  };
  try {
    session.stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        mandatory: {
          chromeMediaSource: "tab",
          chromeMediaSourceId: String(message.streamId),
          maxFrameRate: 1
        }
      }
    });
    const [track] = session.stream.getVideoTracks();
    if (!track) throw new Error("Edge returned a tab stream without a video track.");
    track.contentHint = "detail";
    track.addEventListener("mute", () => {
      if (session.stopped || session !== activeSession) return;
      void notifyStatus({
        phase: "source_frozen",
        message: "Edge temporarily muted the locked tab stream; PhoenixGuard is holding the source without focusing the browser.",
        lastError: "The captured tab video track is muted."
      });
    });
    track.addEventListener("unmute", () => {
      if (session.stopped || session !== activeSession) return;
      session.lastVideoFrameAtMs = Date.now();
      void notifyStatus({
        phase: serverArmed && status.acceptedFrames > 0 ? "live" : "starting",
        message: "The selected chart tab resumed background rendering.",
        lastError: ""
      });
    });
    track.addEventListener("ended", () => {
      if (session.stopped) return;
      if (session === activeSession) {
        void stopAllCapture("The locked chart-tab stream ended.");
      } else if (session === candidateSession) {
        void cancelCandidate("The candidate chart-tab stream ended before confirmation.");
      }
    }, {once: true});
    video.srcObject = session.stream;
    await video.play();
    await waitForVideo(session);
    refreshMediaPlayback(session);
    watchPresentedFrames(session);
    return session;
  } catch (error) {
    stopSession(session);
    throw error;
  }
}

async function claimSource(session) {
  const response = await fetch(sourceControlClaimEndpoint(session.config), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${session.config.token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      source_id: session.config.sourceId,
      sequence_id: session.sequenceId,
      source_type: "browser_tab_roi_capture",
      selection_id: session.selectionId,
      display_name: String(session.tab.title || "Selected Edge chart").slice(0, 180),
      coordinate_space: "edge_tab_roi_v1"
    }),
    cache: "no-store"
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(errorText(payload.detail || payload.message || `Source claim returned HTTP ${response.status}.`));
    error.httpStatus = response.status;
    throw error;
  }
  const generation = Number(payload.source_generation || payload.source_control?.source_generation || 0);
  const leaseId = String(payload.source_lease_id || payload.source_control?.source_lease_id || "");
  if (!Number.isInteger(generation) || generation <= 0 || !leaseId) {
    throw new Error("PhoenixGuard accepted the source claim without a valid generation and lease ID.");
  }
  session.sourceGeneration = generation;
  session.sourceLeaseId = leaseId;
  return payload;
}

async function killSource(session, reason) {
  if (!session?.config?.token) return;
  try {
    await fetch(sourceControlKillEndpoint(session.config), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.config.token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(sourceControlKillPayload({
        sourceId: session.config.sourceId,
        sequenceId: session.sequenceId,
        sourceGeneration: session.sourceGeneration,
        sourceLeaseId: session.sourceLeaseId,
        reason
      })),
      cache: "no-store"
    });
  } catch (error) {
    console.debug("PhoenixGuard source-control kill acknowledgement unavailable.", error);
  }
}

function stopSession(session) {
  if (!session || session.stopped) return;
  session.stopped = true;
  if (session.frameCallbackId && typeof session.video.cancelVideoFrameCallback === "function") {
    session.video.cancelVideoFrameCallback(session.frameCallbackId);
  }
  if (session.stream) {
    for (const track of session.stream.getTracks()) track.stop();
  }
  session.video.pause();
  session.video.srcObject = null;
  session.video.remove();
}

function resetFramePipeline() {
  sampleTimer = clearTimer(sampleTimer);
  uploadTimer = clearTimer(uploadTimer);
  contractTimer = clearTimer(contractTimer);
  pendingFrame = null;
  uploadInFlight = false;
  previousQueuedProbe = null;
  lastQueuedAt = 0;
  lastAcceptedAtMs = 0;
  lastVisualChangeAtMs = 0;
  serverArmed = false;
  signatureRequired = false;
  status = initialStatus({
    phase: "starting",
    message: "The selected chart region is waiting for its first accepted frame.",
    ...statusIdentity()
  });
}

async function refreshServerContract() {
  contractTimer = clearTimer(contractTimer);
  if (!activeSession || samplingPausedForSelector) return;
  const config = activeSession.config;
  try {
    const response = await fetch(frameIngestConfigEndpoint(config), {method: "GET", cache: "no-store"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`Frame-ingest config returned HTTP ${response.status}.`);
    const advertisedInterval = Number(payload.min_interval_sec ?? payload.readiness?.min_interval_sec ?? 10);
    status.uploadMinIntervalSec = Math.max(
      SAMPLE_INTERVAL_MS / 1000,
      Number.isFinite(advertisedInterval) ? advertisedInterval : 10
    );
    serverArmed = Boolean(payload.readiness?.armed);
    signatureRequired = Boolean(payload.readiness?.signature_required);
    if (signatureRequired && !config.signingSecret) {
      serverArmed = false;
      await notifyStatus({
        phase: "configuration_required",
        message: "Frame ingest requires an HMAC signing secret. Save it in extension options and reselect the chart.",
        lastError: "Missing required frame-ingest signing secret."
      });
      return;
    }
    if (!serverArmed) {
      await notifyStatus({
        phase: "waiting_for_ingest",
        message: "The chart region is locked, but PhoenixGuard frame ingest is not armed yet.",
        lastError: ""
      });
      contractTimer = setTimeout(refreshServerContract, 30_000);
      return;
    }
    await notifyStatus({
      phase: status.acceptedFrames > 0 ? "live" : "starting",
      message: status.acceptedFrames > 0
        ? "The selected chart region is streaming in the background."
        : "The chart region is locked; waiting for the first accepted frame.",
      lastError: ""
    });
    void drainLatestFrame();
  } catch (error) {
    serverArmed = false;
    await notifyStatus({
      phase: "degraded",
      message: "The chart region remains locked; PhoenixGuard ingest is temporarily unreachable.",
      lastError: errorText(error)
    });
    contractTimer = setTimeout(refreshServerContract, 15_000);
  }
}

function captureProbe() {
  probeContext.drawImage(frameCanvas, 0, 0, probeCanvas.width, probeCanvas.height);
  const rgba = probeContext.getImageData(0, 0, probeCanvas.width, probeCanvas.height).data;
  const grayscale = new Uint8Array(probeCanvas.width * probeCanvas.height);
  for (let pixel = 0, offset = 0; pixel < grayscale.length; pixel += 1, offset += 4) {
    grayscale[pixel] = Math.round((rgba[offset] + rgba[offset + 1] + rgba[offset + 2]) / 3);
  }
  return grayscale;
}

async function sampleFrame() {
  sampleTimer = clearTimer(sampleTimer);
  if (!activeSession || samplingPausedForSelector) return;
  try {
    if (!activeFresh()) {
      pendingFrame = null;
      await notifyStatus({
        phase: "source_frozen",
        message: "The tab stream is connected, but Edge has not presented a fresh chart frame. Upload is paused.",
        lastError: "No freshly presented tab video frame inside the bounded freshness window."
      });
      return;
    }
    const sourceWidth = Number(activeSession.video.videoWidth) || 0;
    const sourceHeight = Number(activeSession.video.videoHeight) || 0;
    if (sourceWidth < 64 || sourceHeight < 64) throw new Error("The locked chart stream has no usable video frame.");
    const crop = mapNormalizedRegionToPixels(activeSession.region.normalized, sourceWidth, sourceHeight);
    const scale = Math.min(1, activeSession.config.maxWidth / crop.width);
    const width = Math.max(64, Math.round(crop.width * scale));
    const height = Math.max(64, Math.round(crop.height * scale));
    if (frameCanvas.width !== width || frameCanvas.height !== height) {
      frameCanvas.width = width;
      frameCanvas.height = height;
    }
    frameContext.drawImage(
      activeSession.video,
      crop.x,
      crop.y,
      crop.width,
      crop.height,
      0,
      0,
      width,
      height
    );
    const probe = captureProbe();
    const materialDelta = meanAbsoluteDifference(previousQueuedProbe, probe);
    const now = Date.now();
    const heartbeatDue = now - lastQueuedAt >= activeSession.config.heartbeatSec * 1000;
    const materialChange = !previousQueuedProbe || materialDelta >= activeSession.config.materialDeltaThreshold;
    status.sampledFrames += 1;
    status.cropPixels = crop;
    if (materialChange) lastVisualChangeAtMs = now;

    if (materialChange || heartbeatDue) {
      const blob = await canvasBlob(frameCanvas, "image/jpeg", activeSession.config.jpegQuality);
      if (pendingFrame) status.replacedFrames += 1;
      pendingFrame = {
        blob,
        captureEpochMs: now,
        width,
        height,
        crop,
        materialDelta,
        materialChange,
        heartbeatDue,
        transportFrameAgeMs: currentVideoAge(),
        visualChangeAgeMs: lastVisualChangeAtMs ? now - lastVisualChangeAtMs : -1,
        presentedFrames: activeSession.presentedFrames,
        mediaTime: activeSession.mediaTime
      };
      previousQueuedProbe = probe;
      lastQueuedAt = now;
      status.queuedFrames += 1;
      void drainLatestFrame();
    }
  } catch (error) {
    await notifyStatus({
      phase: "degraded",
      message: "The chart stream remains locked, but its latest selected-region sample could not be encoded.",
      lastError: errorText(error)
    });
  } finally {
    if (activeSession && !samplingPausedForSelector) sampleTimer = setTimeout(sampleFrame, SAMPLE_INTERVAL_MS);
  }
}

function scheduleUpload(delayMs) {
  uploadTimer = clearTimer(uploadTimer);
  if (activeSession && !samplingPausedForSelector) {
    uploadTimer = setTimeout(() => void drainLatestFrame(), Math.max(0, delayMs));
  }
}

async function uploadFrame(frame) {
  const session = activeSession;
  if (!session) throw new Error("The selected chart source ended before upload.");
  const config = session.config;
  const frameId = status.frameId + 1;
  const endpoint = frameIngestEndpoint(config);
  const endpointUrl = new URL(endpoint);
  const form = new FormData();
  form.append("frame", frame.blob, `edge_roi_${String(frameId).padStart(8, "0")}.jpg`);
  form.append("source_id", config.sourceId);
  form.append("source_url", sanitizeSourceUrl(session.tab.url));
  form.append("symbol", config.symbol);
  form.append("timeframe", config.timeframe);
  form.append("sequence_id", session.sequenceId);
  form.append("capture_epoch_ms", String(frame.captureEpochMs));
  form.append("frame_id", String(frameId));
  form.append("source_generation", String(session.sourceGeneration));
  form.append("source_lease_id", session.sourceLeaseId);
  form.append("metadata_json", JSON.stringify({
    source_type: "browser_tab_roi_capture",
    browser: "Microsoft Edge",
    extension_id: chrome.runtime.id,
    extension_version: chrome.runtime.getManifest().version,
    capture_mode: "tabCapture_offscreen_roi",
    coordinate_space: "edge_tab_roi_v1",
    browser_chrome_included: false,
    focus_policy: "never_activate_raise_or_focus_tabs",
    sample_fps: SAMPLE_FPS,
    latest_frame_wins: true,
    selection_id: session.selectionId,
    region_revision: session.sequenceId,
    source_generation: session.sourceGeneration,
    source_lease_id: session.sourceLeaseId,
    roi_normalized: session.region.normalized,
    roi_css: session.region.rectCss,
    roi_source_pixels: frame.crop,
    source_surface_width: frame.crop.sourceWidth,
    source_surface_height: frame.crop.sourceHeight,
    material_change: frame.materialChange,
    material_delta: Number(frame.materialDelta.toFixed(6)),
    heartbeat_frame: frame.heartbeatDue,
    capture_width: frame.width,
    capture_height: frame.height,
    locked_tab_id: session.tab.id,
    locked_tab_title: session.tab.title,
    locked_origin: session.tab.origin,
    source_render_fresh: frame.transportFrameAgeMs <= SOURCE_FREEZE_TIMEOUT_MS,
    transport_frame_age_ms: frame.transportFrameAgeMs,
    visual_change_age_ms: frame.visualChangeAgeMs,
    presented_frames: frame.presentedFrames,
    media_time: frame.mediaTime
  }));

  const headers = {Authorization: `Bearer ${config.token}`};
  if (config.signingSecret) {
    const timestamp = String(Date.now());
    const nonce = crypto.randomUUID();
    const frameSha256 = await sha256Hex(frame.blob);
    const canonical = canonicalFrameSignaturePayload({
      path: endpointUrl.pathname,
      sessionId: config.sessionId,
      sourceId: config.sourceId,
      sequenceId: session.sequenceId,
      frameId,
      captureEpochMs: frame.captureEpochMs,
      frameSha256,
      timestamp,
      nonce
    });
    const signature = await hmacSha256Hex(config.signingSecret, canonical);
    headers["X-PhoenixGuard-Signature-Alg"] = "HMAC-SHA256-V1";
    headers["X-PhoenixGuard-Timestamp"] = timestamp;
    headers["X-PhoenixGuard-Nonce"] = nonce;
    headers["X-PhoenixGuard-Signature"] = `v1=${signature}`;
  }

  const response = await fetch(endpoint, {method: "POST", headers, body: form, cache: "no-store"});
  const payload = await response.json().catch(() => ({}));
  status.frameId = frameId;
  if (!response.ok) {
    const detail = errorText(payload.detail || payload.message || `HTTP ${response.status}`);
    const error = new Error(detail);
    error.httpStatus = response.status;
    throw error;
  }
  return payload;
}

async function drainLatestFrame() {
  uploadTimer = clearTimer(uploadTimer);
  if (!activeSession || samplingPausedForSelector || uploadInFlight || !pendingFrame || !serverArmed) return;
  if (!activeFresh()) {
    pendingFrame = null;
    await notifyStatus({phase: "source_frozen", message: "A stale rendered frame was blocked before upload."});
    return;
  }
  const minIntervalMs = Math.max(SAMPLE_INTERVAL_MS, Number(status.uploadMinIntervalSec || 10) * 1000);
  const elapsed = Date.now() - lastAcceptedAtMs;
  if (lastAcceptedAtMs > 0 && elapsed < minIntervalMs) {
    scheduleUpload(minIntervalMs - elapsed);
    return;
  }
  const frame = pendingFrame;
  pendingFrame = null;
  uploadInFlight = true;
  try {
    await uploadFrame(frame);
    lastAcceptedAtMs = Date.now();
    status.acceptedFrames += 1;
    await notifyStatus({
      phase: "live",
      message: "The selected chart region is streaming in the background.",
      lastAcceptedAt: new Date(lastAcceptedAtMs).toISOString(),
      lastError: ""
    });
  } catch (error) {
    status.rejectedFrames += 1;
    const httpStatus = Number(error?.httpStatus || 0);
    if (httpStatus === 409 || httpStatus === 410) {
      await stopAllCapture("PhoenixGuard rejected this superseded or killed chart-source lease.", true, false);
    } else if (httpStatus === 401 || httpStatus === 403) {
      serverArmed = false;
      await notifyStatus({
        phase: "configuration_required",
        message: "Frame ingest rejected the token or source scope. Fix options, then reselect the chart.",
        lastError: errorText(error)
      });
    } else if (httpStatus === 429) {
      await notifyStatus({
        phase: "waiting_for_ingest",
        message: "PhoenixGuard is applying its advertised upload interval; the newest chart frame remains authoritative.",
        lastError: errorText(error)
      });
      scheduleUpload(minIntervalMs);
    } else {
      await notifyStatus({
        phase: "degraded",
        message: "Chart capture continues in the background while frame ingest recovers.",
        lastError: errorText(error)
      });
      contractTimer = clearTimer(contractTimer);
      contractTimer = setTimeout(refreshServerContract, 15_000);
    }
  } finally {
    uploadInFlight = false;
    if (activeSession && pendingFrame && serverArmed && !samplingPausedForSelector) {
      scheduleUpload(Math.max(0, minIntervalMs - (Date.now() - lastAcceptedAtMs)));
    }
  }
}

async function prepareCandidate(message) {
  await cancelCandidate("A newer chart selection replaced the previous candidate.", false);
  const selectionId = String(message.selectionId || "");
  if (!selectionId) return {ok: false, error: "Candidate selection ID is missing."};
  try {
    if (message.reuseActiveStream) {
      if (!activeSession || activeSession.tab.id !== Number(message.tab?.id || 0)) {
        throw new Error("The active chart stream is unavailable for region reselection.");
      }
      candidateSession = {
        ...activeSession,
        tab: {...activeSession.tab},
        selectionId,
        sequenceId: String(message.sequenceId || ""),
        config: normalizeConfig(message.config),
        reusesActive: true,
        stopped: false
      };
      samplingPausedForSelector = true;
      sampleTimer = clearTimer(sampleTimer);
      uploadTimer = clearTimer(uploadTimer);
      pendingFrame = null;
    } else {
      candidateSession = await createCapturedSession(message);
    }
    candidateTimer = setTimeout(() => {
      void (async () => {
        try {
          const response = await chrome.runtime.sendMessage({
            type: "OFFSCREEN_CANDIDATE_TIMEOUT_V1",
            selectionId: candidateSession?.selectionId || "",
            candidateTabId: candidateSession?.tab.id || 0
          });
          if (response?.ok) return;
        } catch {
          // If the service worker cannot be reached, release the bounded candidate locally.
        }
        await cancelCandidate("Chart-region selection timed out before confirmation.");
      })();
    }, Math.min(SELECTION_TIMEOUT_MS, Number(message.timeoutMs) || SELECTION_TIMEOUT_MS));
    await notifyStatus({
      phase: "selecting",
      message: "Select the exact chart rectangle. Candidate pixels are not being uploaded.",
      lastError: ""
    });
    return {ok: true, status};
  } catch (error) {
    await cancelCandidate("Candidate chart stream failed.", false);
    return {ok: false, error: errorText(error)};
  }
}

async function cancelCandidate(reason = "Chart-region selection cancelled.", announce = true) {
  candidateTimer = clearTimer(candidateTimer);
  const candidate = candidateSession;
  candidateSession = null;
  if (candidate && !candidate.reusesActive) stopSession(candidate);
  if (candidate?.reusesActive) samplingPausedForSelector = false;
  if (activeSession && !samplingPausedForSelector) {
    if (candidate?.reusesActive) await waitForCleanVideoFrame(activeSession);
    if (!sampleTimer) sampleTimer = setTimeout(sampleFrame, 150);
    if (announce) {
      await notifyStatus({
        phase: activeFresh() && serverArmed && status.acceptedFrames > 0 ? "live" : "starting",
        message: "Existing chart capture preserved; the new selection was cancelled.",
        lastError: ""
      });
    }
  } else if (announce) {
    await notifyStatus({
      phase: "stopped",
      message: reason,
      lockedTabId: 0,
      sequenceId: "",
      region: null,
      sourceRenderFresh: false
    });
  }
  return {ok: true, status};
}

async function commitCandidate(message) {
  if (!candidateSession || candidateSession.selectionId !== String(message.selectionId || "")) {
    return {ok: false, error: "The candidate chart selection expired or was replaced."};
  }
  const normalized = normalizeRegionSelection(message.region);
  if (!normalized.ok) return {ok: false, error: normalized.reason};
  candidateTimer = clearTimer(candidateTimer);
  const candidate = candidateSession;
  candidate.region = normalized.region;
  samplingPausedForSelector = true;
  sampleTimer = clearTimer(sampleTimer);
  uploadTimer = clearTimer(uploadTimer);
  pendingFrame = null;
  try {
    await waitForUploadIdle();
    if (candidateSession !== candidate || candidate.stopped) {
      throw new Error("The candidate chart stream ended before source promotion.");
    }
    await claimSource(candidate);
    if (candidateSession !== candidate || candidate.stopped) {
      await killSource(candidate, "Candidate source was stopped during claim promotion.");
      throw new Error("The candidate chart stream was stopped during source promotion.");
    }
  } catch (error) {
    samplingPausedForSelector = false;
    if (activeSession && !sampleTimer) sampleTimer = setTimeout(sampleFrame, 150);
    return {ok: false, error: `PhoenixGuard source claim failed: ${errorText(error)}`};
  }
  candidateSession = null;

  if (candidate.reusesActive) {
    activeSession.selectionId = candidate.selectionId;
    activeSession.sequenceId = candidate.sequenceId;
    activeSession.region = candidate.region;
    activeSession.config = candidate.config;
    activeSession.sourceGeneration = candidate.sourceGeneration;
    activeSession.sourceLeaseId = candidate.sourceLeaseId;
  } else {
    const previous = activeSession;
    activeSession = candidate;
    if (previous) stopSession(previous);
  }
  samplingPausedForSelector = false;
  resetFramePipeline();
  await waitForCleanVideoFrame(activeSession);
  await notifyStatus({
    phase: "starting",
    message: "The chart region is locked; waiting for the first accepted clean frame.",
    lastError: ""
  });
  await refreshServerContract();
  await sampleFrame();
  return {ok: true, status};
}

async function stopAllCapture(reason = "Chart capture stopped.", announce = true, notifyBackend = true) {
  candidateTimer = clearTimer(candidateTimer);
  sampleTimer = clearTimer(sampleTimer);
  uploadTimer = clearTimer(uploadTimer);
  contractTimer = clearTimer(contractTimer);
  const active = activeSession;
  const candidate = candidateSession;
  activeSession = null;
  candidateSession = null;
  if (candidate && !candidate.reusesActive && candidate !== active) stopSession(candidate);
  if (active && notifyBackend) await killSource(active, reason);
  if (active) stopSession(active);
  samplingPausedForSelector = false;
  pendingFrame = null;
  uploadInFlight = false;
  previousQueuedProbe = null;
  lastQueuedAt = 0;
  lastAcceptedAtMs = 0;
  lastVisualChangeAtMs = 0;
  serverArmed = false;
  signatureRequired = false;
  if (announce) {
    status = initialStatus({phase: "stopped", message: reason});
    await notifyStatus({phase: "stopped", message: reason, sourceRenderFresh: false});
  }
  return {ok: true, status};
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.target !== "offscreen") return false;
  if (message.type === "PREPARE_CAPTURE_CANDIDATE_V1") {
    void prepareCandidate(message).then(sendResponse);
    return true;
  }
  if (message.type === "COMMIT_CAPTURE_REGION_V1") {
    void commitCandidate(message).then(sendResponse);
    return true;
  }
  if (message.type === "CANCEL_CAPTURE_CANDIDATE_V1") {
    if (candidateSession && message.selectionId && candidateSession.selectionId !== message.selectionId) {
      sendResponse({ok: false, error: "Candidate selection ID mismatch.", status});
      return false;
    }
    void cancelCandidate(message.reason || "Chart-region selection cancelled.").then(sendResponse);
    return true;
  }
  if (message.type === "STOP_ALL_CAPTURE_V1") {
    void stopAllCapture(message.reason || "Chart capture stopped.").then(sendResponse);
    return true;
  }
  if (message.type === "GET_STATUS") {
    sendResponse({ok: true, status});
    return false;
  }
  return false;
});
