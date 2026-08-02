import {
  DEFAULT_CONFIG,
  frameIngestConfigEndpoint,
  initialStatus,
  normalizeConfig,
  validateConfig
} from "./common.js";

const CONFIG_KEY = "captureConfig";
const STATUS_KEY = "captureStatus";
const fields = [
  "baseUrl",
  "sessionId",
  "sourceId",
  "token",
  "signingSecret",
  "symbol",
  "timeframe",
  "maxWidth",
  "jpegQuality",
  "materialDeltaThreshold",
  "heartbeatSec"
];
const elements = Object.fromEntries(fields.map((id) => [id, document.getElementById(id)]));
const statusPanel = document.querySelector(".status");
const phase = document.getElementById("phase");
const message = document.getElementById("message");
const lockedTab = document.getElementById("lockedTab");
const sampling = document.getElementById("sampling");
const uploadInterval = document.getElementById("uploadInterval");
const acceptedFrames = document.getElementById("acceptedFrames");
const replacedFrames = document.getElementById("replacedFrames");
const lastAccepted = document.getElementById("lastAccepted");
const lastError = document.getElementById("lastError");
const feedback = document.getElementById("feedback");

function setFeedback(text, kind = "") {
  feedback.textContent = text;
  feedback.dataset.kind = kind;
}

function readForm() {
  return normalizeConfig(Object.fromEntries(fields.map((id) => [id, elements[id].value])));
}

function writeForm(config) {
  const normalized = normalizeConfig(config);
  for (const id of fields) elements[id].value = normalized[id] ?? "";
}

function renderStatus(rawStatus) {
  const value = initialStatus(rawStatus || {});
  statusPanel.dataset.phase = value.phase;
  phase.textContent = String(value.phase || "idle").replaceAll("_", " ").toUpperCase();
  message.textContent = value.message;
  lockedTab.textContent = value.lockedTabId > 0 ? value.lockedTitle || `Tab ${value.lockedTabId}` : "None";
  lockedTab.title = value.lockedUrl || "";
  sampling.textContent = `${Number(value.sampleFps || 0.25).toFixed(2)} FPS`;
  uploadInterval.textContent = value.uploadMinIntervalSec > 0
    ? `At least ${value.uploadMinIntervalSec}s`
    : "Server controlled";
  acceptedFrames.textContent = `${Number(value.acceptedFrames || 0)} frames`;
  replacedFrames.textContent = String(Number(value.replacedFrames || 0));
  lastAccepted.textContent = value.lastAcceptedAt
    ? new Date(value.lastAcceptedAt).toLocaleString()
    : "Never";
  lastError.hidden = !value.lastError;
  lastError.textContent = value.lastError ? `Last error: ${value.lastError}` : "";
}

async function load() {
  const stored = await chrome.storage.local.get([CONFIG_KEY, STATUS_KEY]);
  writeForm(stored[CONFIG_KEY] || DEFAULT_CONFIG);
  renderStatus(stored[STATUS_KEY] || initialStatus());
}

document.getElementById("configForm").addEventListener("submit", (event) => {
  event.preventDefault();
  void (async () => {
    const config = readForm();
    const validation = validateConfig(config);
    if (!validation.ok) {
      setFeedback(validation.reason, "error");
      return;
    }
    await chrome.storage.local.set({[CONFIG_KEY]: validation.config});
    setFeedback("Saved. Stop any existing stream, activate Pocket Option, then click the extension icon once.", "ok");
  })();
});

document.getElementById("testConnection").addEventListener("click", () => {
  void (async () => {
    const config = readForm();
    setFeedback("Checking PhoenixGuard frame ingest…");
    try {
      const response = await fetch(frameIngestConfigEndpoint(config), {cache: "no-store"});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const armed = Boolean(payload.readiness?.armed);
      const interval = Number(payload.min_interval_sec ?? payload.readiness?.min_interval_sec ?? 0);
      setFeedback(
        armed
          ? `Connected and armed. PhoenixGuard accepts frames no faster than every ${interval || "advertised"} seconds.`
          : "Connected, but frame ingest is not armed. Configure the backend token before trading from this feed.",
        armed ? "ok" : "error"
      );
    } catch (error) {
      setFeedback(`Connection failed: ${error instanceof Error ? error.message : String(error)}`, "error");
    }
  })();
});

document.getElementById("stopCapture").addEventListener("click", () => {
  void chrome.runtime.sendMessage({type: "STOP_CAPTURE_REQUEST"}).then((response) => {
    if (response?.status) renderStatus(response.status);
    setFeedback("Capture stopped.", "ok");
  }).catch((error) => setFeedback(`Stop failed: ${error.message}`, "error"));
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === "local" && changes[STATUS_KEY]?.newValue) {
    renderStatus(changes[STATUS_KEY].newValue);
  }
});

void load();
