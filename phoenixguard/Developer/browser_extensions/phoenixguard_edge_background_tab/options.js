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
const selectedRegion = document.getElementById("selectedRegion");
const sourceLease = document.getElementById("sourceLease");
const renderFreshness = document.getElementById("renderFreshness");
const transportFrame = document.getElementById("transportFrame");
const visualChange = document.getElementById("visualChange");
const sampling = document.getElementById("sampling");
const uploadInterval = document.getElementById("uploadInterval");
const acceptedFrames = document.getElementById("acceptedFrames");
const replacedFrames = document.getElementById("replacedFrames");
const lastAccepted = document.getElementById("lastAccepted");
const lastError = document.getElementById("lastError");
const shortcutError = document.getElementById("shortcutError");
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

function ageText(milliseconds, never = "Never") {
  const value = Number(milliseconds);
  if (!Number.isFinite(value) || value < 0) return never;
  if (value < 1_000) return "Just now";
  if (value < 60_000) return `${Math.round(value / 1_000)}s ago`;
  return `${Math.round(value / 60_000)}m ago`;
}

function renderStatus(rawStatus) {
  const value = initialStatus(rawStatus || {});
  statusPanel.dataset.phase = value.phase;
  phase.textContent = String(value.phase || "idle").replaceAll("_", " ").toUpperCase();
  message.textContent = value.message;
  lockedTab.textContent = value.lockedTabId > 0 ? value.lockedTitle || `Tab ${value.lockedTabId}` : "None";
  lockedTab.title = value.lockedUrl || "";
  const rect = value.region?.rectCss;
  selectedRegion.textContent = rect
    ? `${Math.round(rect.width)} × ${Math.round(rect.height)} at ${Math.round(rect.x)}, ${Math.round(rect.y)}`
    : value.selectionId ? "Selecting…" : "None";
  sourceLease.textContent = value.sourceLeaseActive
    ? `Generation ${Number(value.sourceGeneration || 0)}`
    : "Not claimed";
  renderFreshness.textContent = value.sourceRenderFresh ? "Fresh rendered frames" : value.lockedTabId > 0 ? "Frozen or awaiting frame" : "Not streaming";
  transportFrame.textContent = ageText(value.transportFrameAgeMs);
  visualChange.textContent = ageText(value.visualChangeAgeMs);
  sampling.textContent = `${Number(value.sampleFps || 0.25).toFixed(2)} FPS`;
  uploadInterval.textContent = value.uploadMinIntervalSec > 0
    ? `At least ${value.uploadMinIntervalSec}s`
    : "Server controlled";
  acceptedFrames.textContent = `${Number(value.acceptedFrames || 0)} frames`;
  replacedFrames.textContent = String(Number(value.replacedFrames || 0));
  lastAccepted.textContent = value.lastAcceptedAt ? new Date(value.lastAcceptedAt).toLocaleString() : "Never";
  lastError.hidden = !value.lastError;
  lastError.textContent = value.lastError ? `Last error: ${value.lastError}` : "";
}

async function renderCommands() {
  const commands = await chrome.commands.getAll();
  const byName = Object.fromEntries(commands.map((command) => [command.name, command]));
  const select = byName["select-chart-region"]?.shortcut || "Unassigned";
  const kill = byName["stop-chart-capture"]?.shortcut || "Unassigned";
  document.getElementById("selectShortcut").textContent = select;
  document.getElementById("killShortcut").textContent = kill;
  const missing = [select === "Unassigned" ? "Select / switch" : "", kill === "Unassigned" ? "Kill capture" : ""].filter(Boolean);
  shortcutError.hidden = missing.length === 0;
  shortcutError.textContent = missing.length
    ? `${missing.join(" and ")} shortcut is unassigned. Configure it at edge://extensions/shortcuts.`
    : "";
}

async function load() {
  const stored = await chrome.storage.local.get([CONFIG_KEY, STATUS_KEY]);
  writeForm(stored[CONFIG_KEY] || DEFAULT_CONFIG);
  renderStatus(stored[STATUS_KEY] || initialStatus());
  await renderCommands();
}

document.getElementById("configForm").addEventListener("submit", (event) => {
  event.preventDefault();
  void (async () => {
    const validation = validateConfig(readForm());
    if (!validation.ok) {
      setFeedback(validation.reason, "error");
      return;
    }
    await chrome.storage.local.set({[CONFIG_KEY]: validation.config});
    setFeedback("Saved. Show any HTTP(S) chart and press Ctrl+Shift+8 to select its region.", "ok");
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
      const spaces = payload.browser_extension_coordinate_spaces || payload.leased_coordinate_spaces || [];
      const roiSupported = Array.isArray(spaces) && spaces.includes("edge_tab_roi_v1");
      setFeedback(
        armed && roiSupported
          ? `Connected, armed, and ROI leases supported. Frames are accepted no faster than every ${interval || "advertised"} seconds.`
          : !roiSupported
            ? "Connected, but this PhoenixGuard stack does not advertise edge_tab_roi_v1 source leases. Update and relaunch the stack."
            : "Connected, but frame ingest is not armed. Configure the backend token before selecting a chart.",
        armed && roiSupported ? "ok" : "error"
      );
    } catch (error) {
      setFeedback(`Connection failed: ${error instanceof Error ? error.message : String(error)}`, "error");
    }
  })();
});

document.getElementById("stopCapture").addEventListener("click", () => {
  void chrome.runtime.sendMessage({type: "STOP_CAPTURE_REQUEST"}).then((response) => {
    if (response?.status) renderStatus(response.status);
    setFeedback("All chart capture stopped.", "ok");
  }).catch((error) => setFeedback(`Kill failed: ${error.message}`, "error"));
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === "local" && changes[STATUS_KEY]?.newValue) renderStatus(changes[STATUS_KEY].newValue);
});

void load();
