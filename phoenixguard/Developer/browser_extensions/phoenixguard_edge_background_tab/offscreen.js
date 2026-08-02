import {
  SAMPLE_FPS,
  SAMPLE_INTERVAL_MS,
  canonicalFrameSignaturePayload,
  frameIngestConfigEndpoint,
  frameIngestEndpoint,
  initialStatus,
  meanAbsoluteDifference,
  normalizeConfig,
  sanitizeSourceUrl
} from "./common.js";

const video = document.getElementById("captureVideo");
const frameCanvas = document.getElementById("frameCanvas");
const probeCanvas = document.getElementById("probeCanvas");
const frameContext = frameCanvas.getContext("2d", {alpha: false});
const probeContext = probeCanvas.getContext("2d", {alpha: false, willReadFrequently: true});

let active = false;
let mediaStream = null;
let sampleTimer = null;
let uploadTimer = null;
let contractTimer = null;
let uploadInFlight = false;
let pendingFrame = null;
let previousQueuedProbe = null;
let lastQueuedAt = 0;
let lastAcceptedAtMs = 0;
let serverArmed = false;
let signatureRequired = false;
let config = normalizeConfig();
let lockedTab = {id: 0, title: "", url: ""};
let sequenceId = "";
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

async function notifyStatus(update = {}) {
  status = initialStatus({
    ...status,
    ...update,
    lockedTabId: active ? lockedTab.id : Number(update.lockedTabId || 0),
    lockedTitle: active ? lockedTab.title : String(update.lockedTitle || ""),
    lockedUrl: active ? lockedTab.url : String(update.lockedUrl || ""),
    sequenceId: active ? sequenceId : String(update.sequenceId || ""),
    updatedAt: new Date().toISOString()
  });
  try {
    await chrome.runtime.sendMessage({type: "OFFSCREEN_STATUS", status});
  } catch (error) {
    console.debug("Status delivery deferred until the service worker wakes.", error);
  }
}

function canvasBlob(canvas, type, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("Edge could not encode the sampled tab frame."));
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

async function refreshServerContract() {
  contractTimer = clearTimer(contractTimer);
  if (!active) return;
  try {
    const response = await fetch(frameIngestConfigEndpoint(config), {method: "GET", cache: "no-store"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(`Frame-ingest config returned HTTP ${response.status}.`);
    }
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
        message: "Frame ingest requires an HMAC signing secret. Save it in extension options and relock the tab.",
        lastError: "Missing required frame-ingest signing secret."
      });
      return;
    }
    if (!serverArmed) {
      await notifyStatus({
        phase: "waiting_for_ingest",
        message: "Pocket Option is locked, but PhoenixGuard frame ingest is not armed yet.",
        lastError: ""
      });
      contractTimer = setTimeout(refreshServerContract, 30_000);
      return;
    }
    await notifyStatus({
      phase: status.acceptedFrames > 0 ? "live" : "starting",
      message: status.acceptedFrames > 0
        ? "Background tab capture is live. Edge focus is untouched."
        : "Pocket Option is locked; waiting for the first accepted frame.",
      lastError: ""
    });
    void drainLatestFrame();
  } catch (error) {
    serverArmed = false;
    await notifyStatus({
      phase: "degraded",
      message: "Pocket Option remains locked; PhoenixGuard ingest is temporarily unreachable.",
      lastError: errorText(error)
    });
    contractTimer = setTimeout(refreshServerContract, 15_000);
  }
}

function captureProbe() {
  probeContext.drawImage(video, 0, 0, probeCanvas.width, probeCanvas.height);
  const rgba = probeContext.getImageData(0, 0, probeCanvas.width, probeCanvas.height).data;
  const grayscale = new Uint8Array(probeCanvas.width * probeCanvas.height);
  for (let pixel = 0, offset = 0; pixel < grayscale.length; pixel += 1, offset += 4) {
    grayscale[pixel] = Math.round((rgba[offset] + rgba[offset + 1] + rgba[offset + 2]) / 3);
  }
  return grayscale;
}

async function sampleFrame() {
  if (!active) return;
  try {
    const sourceWidth = Number(video.videoWidth) || 0;
    const sourceHeight = Number(video.videoHeight) || 0;
    if (sourceWidth < 64 || sourceHeight < 64) {
      throw new Error("The locked tab stream has not produced a usable video frame yet.");
    }
    const scale = Math.min(1, config.maxWidth / sourceWidth);
    const width = Math.max(64, Math.round(sourceWidth * scale));
    const height = Math.max(64, Math.round(sourceHeight * scale));
    if (frameCanvas.width !== width || frameCanvas.height !== height) {
      frameCanvas.width = width;
      frameCanvas.height = height;
    }
    frameContext.drawImage(video, 0, 0, width, height);
    const probe = captureProbe();
    const materialDelta = meanAbsoluteDifference(previousQueuedProbe, probe);
    const now = Date.now();
    const heartbeatDue = now - lastQueuedAt >= config.heartbeatSec * 1000;
    const materialChange = !previousQueuedProbe || materialDelta >= config.materialDeltaThreshold;
    status.sampledFrames += 1;

    if (materialChange || heartbeatDue) {
      const blob = await canvasBlob(frameCanvas, "image/jpeg", config.jpegQuality);
      if (pendingFrame) status.replacedFrames += 1;
      pendingFrame = {
        blob,
        captureEpochMs: now,
        width,
        height,
        materialDelta,
        materialChange,
        heartbeatDue
      };
      previousQueuedProbe = probe;
      lastQueuedAt = now;
      status.queuedFrames += 1;
      void drainLatestFrame();
    }
  } catch (error) {
    await notifyStatus({
      phase: "degraded",
      message: "The tab remains locked, but the latest sample could not be encoded.",
      lastError: errorText(error)
    });
  } finally {
    if (active) sampleTimer = setTimeout(sampleFrame, SAMPLE_INTERVAL_MS);
  }
}

function scheduleUpload(delayMs) {
  uploadTimer = clearTimer(uploadTimer);
  if (active) uploadTimer = setTimeout(() => void drainLatestFrame(), Math.max(0, delayMs));
}

async function uploadFrame(frame) {
  const frameId = status.frameId + 1;
  const endpoint = frameIngestEndpoint(config);
  const endpointUrl = new URL(endpoint);
  const form = new FormData();
  form.append("frame", frame.blob, `edge_tab_${String(frameId).padStart(8, "0")}.jpg`);
  form.append("source_id", config.sourceId);
  form.append("source_url", sanitizeSourceUrl(lockedTab.url));
  form.append("symbol", config.symbol);
  form.append("timeframe", config.timeframe);
  form.append("sequence_id", sequenceId);
  form.append("capture_epoch_ms", String(frame.captureEpochMs));
  form.append("frame_id", String(frameId));
  form.append("metadata_json", JSON.stringify({
    source_type: "browser_extension_capture",
    browser: "Microsoft Edge",
    extension_id: chrome.runtime.id,
    extension_version: chrome.runtime.getManifest().version,
    capture_mode: "tabCapture_offscreen",
    coordinate_space: "edge_tab_content_v1",
    browser_chrome_included: false,
    focus_policy: "never_activate_raise_or_focus_tabs",
    sample_fps: SAMPLE_FPS,
    latest_frame_wins: true,
    material_change: frame.materialChange,
    material_delta: Number(frame.materialDelta.toFixed(6)),
    heartbeat_frame: frame.heartbeatDue,
    capture_width: frame.width,
    capture_height: frame.height,
    locked_tab_id: lockedTab.id,
    locked_tab_title: lockedTab.title
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
      sequenceId,
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
  if (!active || uploadInFlight || !pendingFrame || !serverArmed) return;
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
      message: "Background tab capture is live. Edge focus is untouched.",
      lastAcceptedAt: new Date(lastAcceptedAtMs).toISOString(),
      lastError: ""
    });
  } catch (error) {
    status.rejectedFrames += 1;
    const httpStatus = Number(error?.httpStatus || 0);
    if (httpStatus === 401 || httpStatus === 403) {
      serverArmed = false;
      await notifyStatus({
        phase: "configuration_required",
        message: "Frame ingest rejected the token or source scope. Fix extension options, then relock.",
        lastError: errorText(error)
      });
    } else if (httpStatus === 429) {
      await notifyStatus({
        phase: "waiting_for_ingest",
        message: "PhoenixGuard is applying its advertised upload interval; the newest frame remains authoritative.",
        lastError: errorText(error)
      });
      scheduleUpload(minIntervalMs);
    } else {
      await notifyStatus({
        phase: "degraded",
        message: "Capture continues in the background while frame ingest recovers.",
        lastError: errorText(error)
      });
      contractTimer = clearTimer(contractTimer);
      contractTimer = setTimeout(refreshServerContract, 15_000);
    }
  } finally {
    uploadInFlight = false;
    if (active && pendingFrame && serverArmed) {
      scheduleUpload(Math.max(0, minIntervalMs - (Date.now() - lastAcceptedAtMs)));
    }
  }
}

async function waitForVideo() {
  if (video.readyState >= HTMLMediaElement.HAVE_METADATA && video.videoWidth > 0) return;
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("Timed out waiting for Pocket Option tab pixels.")), 8_000);
    video.addEventListener("loadedmetadata", () => {
      clearTimeout(timeout);
      resolve();
    }, {once: true});
  });
}

async function startCapture(message) {
  await stopCapture("Replacing the previous tab stream.", false);
  config = normalizeConfig(message.config);
  lockedTab = {
    id: Number(message.tab?.id || 0),
    title: String(message.tab?.title || ""),
    url: String(message.tab?.url || "")
  };
  sequenceId = String(message.sequenceId || "");
  active = true;
  status = initialStatus({
    phase: "starting",
    message: "Consuming the explicit Edge tabCapture grant in an offscreen document.",
    lockedTabId: lockedTab.id,
    lockedTitle: lockedTab.title,
    lockedUrl: lockedTab.url,
    sequenceId
  });
  await notifyStatus();
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        mandatory: {
          chromeMediaSource: "tab",
          chromeMediaSourceId: String(message.streamId),
          maxFrameRate: 1
        }
      }
    });
    const [videoTrack] = mediaStream.getVideoTracks();
    if (!videoTrack) throw new Error("Edge returned a tab stream without a video track.");
    videoTrack.contentHint = "detail";
    videoTrack.addEventListener("ended", () => {
      if (active) void stopCapture("The locked Edge tab capture track ended.");
    }, {once: true});
    video.srcObject = mediaStream;
    await video.play();
    await waitForVideo();
    await refreshServerContract();
    await sampleFrame();
    return {ok: true};
  } catch (error) {
    const messageText = errorText(error);
    await notifyStatus({phase: "error", message: `Tab capture failed: ${messageText}`, lastError: messageText});
    await stopCapture("Tab capture failed before the first frame.", false);
    return {ok: false, error: messageText};
  }
}

async function stopCapture(reason = "Capture stopped.", announce = true) {
  active = false;
  sampleTimer = clearTimer(sampleTimer);
  uploadTimer = clearTimer(uploadTimer);
  contractTimer = clearTimer(contractTimer);
  pendingFrame = null;
  uploadInFlight = false;
  previousQueuedProbe = null;
  lastQueuedAt = 0;
  lastAcceptedAtMs = 0;
  serverArmed = false;
  signatureRequired = false;
  if (mediaStream) {
    for (const track of mediaStream.getTracks()) track.stop();
  }
  mediaStream = null;
  video.pause();
  video.srcObject = null;
  lockedTab = {id: 0, title: "", url: ""};
  sequenceId = "";
  if (announce) {
    await notifyStatus({
      phase: "stopped",
      message: reason,
      lockedTabId: 0,
      lockedTitle: "",
      lockedUrl: "",
      sequenceId: "",
      lastError: ""
    });
  }
  return {ok: true};
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.target !== "offscreen") return false;
  if (message.type === "START_CAPTURE") {
    void startCapture(message).then(sendResponse);
    return true;
  }
  if (message.type === "STOP_CAPTURE") {
    void stopCapture(message.reason || "Capture stopped.").then(sendResponse);
    return true;
  }
  if (message.type === "GET_STATUS") {
    sendResponse({ok: true, status});
    return false;
  }
  return false;
});
