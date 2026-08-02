import {
  DEFAULT_CONFIG,
  initialStatus,
  makeSequenceId,
  normalizeConfig,
  validateConfig,
  validatePocketOptionTab
} from "./common.js";

const CONFIG_KEY = "captureConfig";
const STATUS_KEY = "captureStatus";
const OFFSCREEN_PATH = "offscreen.html";
const OFFSCREEN_URL = chrome.runtime.getURL(OFFSCREEN_PATH);

let creatingOffscreenDocument = null;
let stoppingCapture = null;

function badgeForPhase(phase) {
  switch (String(phase || "").toLowerCase()) {
    case "live":
      return {text: "LIVE", color: "#168a51"};
    case "locking":
    case "starting":
      return {text: "...", color: "#8a6a16"};
    case "waiting_for_ingest":
    case "degraded":
      return {text: "WAIT", color: "#9b6b11"};
    case "configuration_required":
      return {text: "CFG", color: "#9b6b11"};
    case "error":
      return {text: "ERR", color: "#a63d40"};
    case "stopped":
      return {text: "OFF", color: "#58606d"};
    default:
      return {text: "", color: "#58606d"};
  }
}

async function setActionStatus(status) {
  const badge = badgeForPhase(status.phase);
  await Promise.all([
    chrome.action.setBadgeText({text: badge.text}),
    chrome.action.setBadgeBackgroundColor({color: badge.color}),
    chrome.action.setTitle({
      title: `PhoenixGuard: ${status.message || status.phase || "idle"}`
    })
  ]);
}

async function loadConfig() {
  const stored = await chrome.storage.local.get(CONFIG_KEY);
  return normalizeConfig(stored[CONFIG_KEY] || DEFAULT_CONFIG);
}

async function loadStatus() {
  const stored = await chrome.storage.local.get(STATUS_KEY);
  return initialStatus(stored[STATUS_KEY] || {});
}

async function publishStatus(update) {
  const previous = await loadStatus();
  const next = initialStatus({
    ...previous,
    ...(update || {}),
    updatedAt: new Date().toISOString()
  });
  await chrome.storage.local.set({[STATUS_KEY]: next});
  await setActionStatus(next);
  return next;
}

async function offscreenDocumentExists() {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [OFFSCREEN_URL]
  });
  return contexts.length > 0;
}

async function ensureOffscreenDocument() {
  if (await offscreenDocumentExists()) return;
  if (!creatingOffscreenDocument) {
    creatingOffscreenDocument = chrome.offscreen.createDocument({
      url: OFFSCREEN_PATH,
      reasons: ["USER_MEDIA"],
      justification: "Consume an explicitly granted Pocket Option tabCapture stream without opening or focusing a visible page."
    }).finally(() => {
      creatingOffscreenDocument = null;
    });
  }
  await creatingOffscreenDocument;
}

async function closeOffscreenDocument() {
  if (await offscreenDocumentExists()) {
    await chrome.offscreen.closeDocument();
  }
}

async function sendToOffscreen(message) {
  return chrome.runtime.sendMessage({target: "offscreen", ...message});
}

async function stopCaptureOnce(reason) {
  const previous = await loadStatus();
  try {
    if (await offscreenDocumentExists()) {
      await sendToOffscreen({type: "STOP_CAPTURE", reason});
    }
  } catch (error) {
    console.debug("Offscreen stop acknowledgement unavailable.", error);
  }
  await closeOffscreenDocument();
  return publishStatus({
    phase: "stopped",
    message: reason,
    lockedTabId: 0,
    lockedTitle: "",
    lockedUrl: "",
    sequenceId: "",
    lastError: "",
    acceptedFrames: previous.acceptedFrames || 0
  });
}

async function stopCapture(reason = "Capture stopped by the operator.") {
  if (!stoppingCapture) {
    stoppingCapture = stopCaptureOnce(reason).finally(() => {
      stoppingCapture = null;
    });
  }
  return stoppingCapture;
}

async function lockActivePocketOptionTab(tab) {
  const tabValidation = validatePocketOptionTab(tab);
  if (!tabValidation.ok) {
    await publishStatus({
      phase: "error",
      message: tabValidation.reason,
      lastError: tabValidation.reason,
      lockedTabId: 0
    });
    return;
  }

  const config = await loadConfig();
  const configValidation = validateConfig(config);
  if (!configValidation.ok) {
    await publishStatus({
      phase: "configuration_required",
      message: `${configValidation.reason} Open the extension options, save it, then click once again.`,
      lastError: configValidation.reason,
      lockedTabId: 0
    });
    return;
  }

  const sequenceId = makeSequenceId(tab.id);
  await publishStatus({
    phase: "locking",
    message: "Locking the active Pocket Option tab without changing focus.",
    lockedTabId: tab.id,
    lockedTitle: tab.title || "",
    lockedUrl: tab.url || "",
    sequenceId,
    lastError: ""
  });

  try {
    await ensureOffscreenDocument();
    const streamId = await chrome.tabCapture.getMediaStreamId({targetTabId: tab.id});
    const response = await sendToOffscreen({
      type: "START_CAPTURE",
      streamId,
      tab: {
        id: tab.id,
        title: tab.title || "",
        url: tab.url || ""
      },
      sequenceId,
      config: configValidation.config
    });
    if (!response?.ok) {
      throw new Error(response?.error || "The offscreen capture document did not accept the tab stream.");
    }
  } catch (error) {
    await closeOffscreenDocument().catch(() => undefined);
    const message = error instanceof Error ? error.message : String(error);
    await publishStatus({
      phase: "error",
      message: `Tab lock failed: ${message}`,
      lastError: message,
      lockedTabId: 0,
      sequenceId: ""
    });
  }
}

async function syncFromOffscreen() {
  if (!(await offscreenDocumentExists())) {
    const status = await loadStatus();
    if (status.lockedTabId > 0 && !["stopped", "idle"].includes(status.phase)) {
      await publishStatus({
        phase: "stopped",
        message: "The previous tab stream ended while Edge was not running.",
        lockedTabId: 0,
        sequenceId: ""
      });
    } else {
      await setActionStatus(status);
    }
    return;
  }
  try {
    const response = await sendToOffscreen({type: "GET_STATUS"});
    if (response?.status) await publishStatus(response.status);
  } catch (error) {
    console.debug("Unable to restore offscreen status.", error);
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get([CONFIG_KEY, STATUS_KEY]);
  if (!stored[CONFIG_KEY]) {
    await chrome.storage.local.set({[CONFIG_KEY]: {...DEFAULT_CONFIG}});
  }
  if (!stored[STATUS_KEY]) {
    await chrome.storage.local.set({[STATUS_KEY]: initialStatus()});
  }
  await syncFromOffscreen();
});

chrome.runtime.onStartup.addListener(() => {
  void syncFromOffscreen();
});

chrome.action.onClicked.addListener((tab) => {
  void (async () => {
    const status = await loadStatus();
    if (status.lockedTabId > 0) {
      await stopCapture("Pocket Option background tab capture stopped.");
      return;
    }
    await lockActivePocketOptionTab(tab);
  })();
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void (async () => {
    const status = await loadStatus();
    if (status.lockedTabId === tabId) {
      await stopCapture("The locked Pocket Option tab was closed.");
    }
  })();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!changeInfo.url && !changeInfo.title) return;
  void (async () => {
    const status = await loadStatus();
    if (status.lockedTabId !== tabId) return;
    const validation = validatePocketOptionTab(tab);
    if (!validation.ok) {
      await stopCapture(`The locked tab stopped identifying as Pocket Option: ${validation.reason}`);
    }
  })();
});

chrome.tabCapture.onStatusChanged.addListener((info) => {
  void (async () => {
    const status = await loadStatus();
    if (status.lockedTabId !== info.tabId) return;
    if (["stopped", "error"].includes(info.status)) {
      await stopCapture(`Edge reported the tab capture as ${info.status}.`);
    }
  })();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.target === "offscreen") return false;
  if (message?.type === "OFFSCREEN_STATUS") {
    void publishStatus(message.status || {}).then(() => sendResponse({ok: true}));
    return true;
  }
  if (message?.type === "GET_EXTENSION_STATUS") {
    void Promise.all([loadStatus(), loadConfig()]).then(([status, config]) => {
      sendResponse({ok: true, status, config: {...config, token: "", signingSecret: ""}});
    });
    return true;
  }
  if (message?.type === "STOP_CAPTURE_REQUEST") {
    void stopCapture("Capture stopped from the extension options.").then((status) => {
      sendResponse({ok: true, status});
    });
    return true;
  }
  return false;
});

void syncFromOffscreen();
