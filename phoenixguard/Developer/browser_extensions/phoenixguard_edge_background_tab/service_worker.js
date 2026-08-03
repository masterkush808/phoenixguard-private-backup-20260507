import {
  DEFAULT_CONFIG,
  SELECTION_TIMEOUT_MS,
  initialStatus,
  makeSequenceId,
  normalizeConfig,
  normalizeRegionSelection,
  sanitizeSourceUrl,
  sourceOrigin,
  validateCapturableTab,
  validateConfig
} from "./common.js";

const CONFIG_KEY = "captureConfig";
const STATUS_KEY = "captureStatus";
const OFFSCREEN_PATH = "offscreen.html";
const OFFSCREEN_URL = chrome.runtime.getURL(OFFSCREEN_PATH);

let creatingOffscreenDocument = null;
let stoppingCapture = null;
let startingSelection = null;
let selectionTimeout = null;

function badgeForPhase(phase) {
  switch (String(phase || "").toLowerCase()) {
    case "live": return {text: "LIVE", color: "#168a51"};
    case "selecting": return {text: "ROI", color: "#8a6a16"};
    case "locking":
    case "starting": return {text: "...", color: "#8a6a16"};
    case "source_frozen": return {text: "FRZ", color: "#a63d40"};
    case "waiting_for_ingest":
    case "degraded": return {text: "WAIT", color: "#9b6b11"};
    case "configuration_required": return {text: "CFG", color: "#9b6b11"};
    case "error": return {text: "ERR", color: "#a63d40"};
    case "stopped": return {text: "OFF", color: "#58606d"};
    default: return {text: "", color: "#58606d"};
  }
}

async function setActionStatus(status) {
  const badge = badgeForPhase(status.phase);
  await Promise.all([
    chrome.action.setBadgeText({text: badge.text}),
    chrome.action.setBadgeBackgroundColor({color: badge.color}),
    chrome.action.setTitle({title: `PhoenixGuard: ${status.message || status.phase || "idle"}`})
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
  const next = initialStatus({...previous, ...(update || {}), updatedAt: new Date().toISOString()});
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
      justification: "Consume an explicitly granted chart tab stream and crop its operator-selected region without focusing Edge."
    }).finally(() => {
      creatingOffscreenDocument = null;
    });
  }
  await creatingOffscreenDocument;
}

async function closeOffscreenDocument() {
  if (await offscreenDocumentExists()) await chrome.offscreen.closeDocument();
}

function sendToOffscreen(message) {
  return chrome.runtime.sendMessage({target: "offscreen", ...message});
}

async function dismissSelector(status) {
  const tabId = Number(status?.candidateTabId || 0);
  if (tabId <= 0) return;
  try {
    await chrome.tabs.sendMessage(tabId, {
      type: "DISMISS_ROI_SELECTOR_V1",
      selectionId: String(status.selectionId || "")
    });
  } catch {
    // A navigation or tab close removes the injected selector automatically.
  }
  try {
    await chrome.scripting.removeCSS({target: {tabId, frameIds: [0]}, files: ["roi_selector.css"]});
  } catch {
    // CSS is inert after the selector DOM is removed; navigation also clears it.
  }
}

async function stopCaptureOnce(reason) {
  const previous = await loadStatus();
  await dismissSelector(previous);
  if (selectionTimeout) clearTimeout(selectionTimeout);
  selectionTimeout = null;
  try {
    if (await offscreenDocumentExists()) await sendToOffscreen({type: "STOP_ALL_CAPTURE_V1", reason});
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
    lockedOrigin: "",
    candidateTabId: 0,
    candidateTitle: "",
    candidateUrl: "",
    candidateOrigin: "",
    selectionId: "",
    sequenceId: "",
    region: null,
    cropPixels: null,
    sourceRenderFresh: false,
    lastError: "",
    acceptedFrames: previous.acceptedFrames || 0
  });
}

async function stopCapture(reason = "Chart capture stopped by the operator.") {
  if (!stoppingCapture) {
    stoppingCapture = stopCaptureOnce(reason).finally(() => {
      stoppingCapture = null;
    });
  }
  return stoppingCapture;
}

async function cancelCandidate(reason, statusOverride = null) {
  const status = statusOverride || await loadStatus();
  await dismissSelector(status);
  if (selectionTimeout) clearTimeout(selectionTimeout);
  selectionTimeout = null;
  let response = null;
  try {
    if (await offscreenDocumentExists()) {
      response = await sendToOffscreen({
        type: "CANCEL_CAPTURE_CANDIDATE_V1",
        selectionId: status.selectionId,
        reason
      });
    }
  } catch (error) {
    console.debug("Candidate cancellation acknowledgement unavailable.", error);
  }
  if (response?.status) return publishStatus(response.status);
  return publishStatus({
    phase: status.lockedTabId > 0 ? status.phase === "selecting" ? "live" : status.phase : "stopped",
    message: status.lockedTabId > 0 ? "Existing chart capture preserved; region selection was cancelled." : reason,
    selectionId: "",
    candidateTabId: 0,
    candidateTitle: "",
    candidateUrl: "",
    candidateOrigin: ""
  });
}

async function injectSelector(tabId) {
  await chrome.scripting.insertCSS({target: {tabId, frameIds: [0]}, files: ["roi_selector.css"]});
  await chrome.scripting.executeScript({target: {tabId, frameIds: [0]}, files: ["roi_selector.js"]});
}

async function beginRegionSelectionOnce(tab) {
  const tabValidation = validateCapturableTab(tab);
  if (!tabValidation.ok) {
    await publishStatus({phase: "error", message: tabValidation.reason, lastError: tabValidation.reason});
    return {ok: false, error: tabValidation.reason};
  }
  const configValidation = validateConfig(await loadConfig());
  if (!configValidation.ok) {
    await publishStatus({
      phase: "configuration_required",
      message: `${configValidation.reason} Save extension options, then select the chart again.`,
      lastError: configValidation.reason
    });
    return {ok: false, error: configValidation.reason};
  }

  let previous = await loadStatus();
  if (previous.selectionId) {
    await cancelCandidate("A newer chart-region selection replaced the unfinished selector.", previous);
    previous = await loadStatus();
  }

  const selectionId = crypto.randomUUID();
  const sequenceId = makeSequenceId(tab.id);
  const candidate = {
    id: tab.id,
    title: String(tab.title || "").slice(0, 180),
    url: sanitizeSourceUrl(tab.url).slice(0, 2048),
    origin: sourceOrigin(tab.url)
  };

  try {
    const offscreenWasPresent = await offscreenDocumentExists();
    await ensureOffscreenDocument();
    let liveOffscreenStatus = null;
    if (offscreenWasPresent) {
      try {
        liveOffscreenStatus = (await sendToOffscreen({type: "GET_STATUS"}))?.status || null;
      } catch {
        liveOffscreenStatus = null;
      }
    }
    await injectSelector(tab.id);
    const sameTabReselection = Number(liveOffscreenStatus?.lockedTabId || 0) === tab.id &&
      String(liveOffscreenStatus?.lockedOrigin || "") === candidate.origin;
    let streamId = "";
    if (!sameTabReselection) {
      streamId = await chrome.tabCapture.getMediaStreamId({targetTabId: tab.id});
    }
    const prepared = await sendToOffscreen({
      type: "PREPARE_CAPTURE_CANDIDATE_V1",
      streamId,
      reuseActiveStream: sameTabReselection,
      tab: candidate,
      selectionId,
      sequenceId,
      config: configValidation.config,
      timeoutMs: SELECTION_TIMEOUT_MS
    });
    if (!prepared?.ok) throw new Error(prepared?.error || "The offscreen document rejected the provisional chart stream.");

    const nextStatus = await publishStatus({
      ...(prepared.status || {}),
      phase: "selecting",
      message: "Drag over the exact chart region, then confirm it. No selector pixels are uploaded.",
      selectionId,
      candidateTabId: candidate.id,
      candidateTitle: candidate.title,
      candidateUrl: candidate.url,
      candidateOrigin: candidate.origin,
      lastError: ""
    });
    const opened = await chrome.tabs.sendMessage(tab.id, {type: "OPEN_ROI_SELECTOR_V1", selectionId});
    if (!opened?.ok) throw new Error(opened?.error || "The chart-region selector did not open.");

    if (selectionTimeout) clearTimeout(selectionTimeout);
    selectionTimeout = setTimeout(() => {
      void (async () => {
        await dismissSelector(nextStatus);
        const current = await loadStatus();
        if (current.selectionId === selectionId) {
          await cancelCandidate("Chart-region selection timed out after 60 seconds.", current);
        }
      })();
    }, SELECTION_TIMEOUT_MS);
    return {ok: true, status: nextStatus};
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const recovered = await cancelCandidate(`Chart-region selection failed: ${message}`).catch(() => null);
    if (Number(recovered?.lockedTabId || 0) > 0) {
      await publishStatus({lastError: `New selection failed while the existing chart remained locked: ${message}`});
    } else {
      await publishStatus({phase: "error", message: `Chart-region selection failed: ${message}`, lastError: message});
    }
    return {ok: false, error: message};
  }
}

async function beginRegionSelection(tab) {
  if (!startingSelection) {
    startingSelection = beginRegionSelectionOnce(tab).finally(() => {
      startingSelection = null;
    });
  }
  return startingSelection;
}

async function resolveCommandTab(tab) {
  if (tab && Number.isInteger(tab.id) && tab.id > 0) return tab;
  const candidates = await chrome.tabs.query({active: true, lastFocusedWindow: true});
  return candidates[0] || null;
}

async function commitSelection(message, sender) {
  const status = await loadStatus();
  const senderTabId = Number(sender?.tab?.id || 0);
  if (sender?.id !== chrome.runtime.id || Number(sender?.frameId || 0) !== 0) {
    return {ok: false, error: "Untrusted selector sender."};
  }
  if (!status.selectionId || message.selectionId !== status.selectionId || senderTabId !== status.candidateTabId) {
    return {ok: false, error: "This selector is no longer the active PhoenixGuard selection."};
  }
  if (sourceOrigin(sender.tab?.url) !== status.candidateOrigin) {
    await cancelCandidate("The selected tab navigated before its region was confirmed.", status);
    return {ok: false, error: "The selected tab origin changed."};
  }
  const normalized = normalizeRegionSelection(message.selection);
  if (!normalized.ok) return {ok: false, error: normalized.reason};
  await dismissSelector(status);
  if (selectionTimeout) clearTimeout(selectionTimeout);
  selectionTimeout = null;

  try {
    const response = await sendToOffscreen({
      type: "COMMIT_CAPTURE_REGION_V1",
      selectionId: status.selectionId,
      region: normalized.region
    });
    if (!response?.ok) throw new Error(response?.error || "The selected chart region was not committed.");
    if (response.status) await publishStatus(response.status);
    return {ok: true};
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error);
    await cancelCandidate(`Chart-region commit failed: ${text}`, status);
    return {ok: false, error: text};
  }
}

async function syncFromOffscreen() {
  if (!(await offscreenDocumentExists())) {
    const status = await loadStatus();
    if (status.lockedTabId > 0 || status.candidateTabId > 0) {
      await publishStatus({
        phase: "stopped",
        message: "The previous chart stream ended while Edge was not running.",
        lockedTabId: 0,
        candidateTabId: 0,
        selectionId: "",
        sourceRenderFresh: false
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
  if (!stored[CONFIG_KEY]) await chrome.storage.local.set({[CONFIG_KEY]: {...DEFAULT_CONFIG}});
  if (!stored[STATUS_KEY]) await chrome.storage.local.set({[STATUS_KEY]: initialStatus()});
  await syncFromOffscreen();
});

chrome.runtime.onStartup.addListener(() => void syncFromOffscreen());

chrome.action.onClicked.addListener((tab) => {
  void beginRegionSelection(tab);
});

chrome.commands.onCommand.addListener((command, tab) => {
  if (command === "select-chart-region") {
    void resolveCommandTab(tab).then(beginRegionSelection);
  } else if (command === "stop-chart-capture") {
    void stopCapture("Emergency kill switch: all chart capture stopped.");
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void (async () => {
    const status = await loadStatus();
    if (status.candidateTabId === tabId) {
      await cancelCandidate("The chart tab being selected was closed.", status);
    }
    if (status.lockedTabId === tabId) await stopCapture("The locked chart tab was closed.");
  })();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!changeInfo.url && changeInfo.status !== "loading") return;
  void (async () => {
    const status = await loadStatus();
    if (status.candidateTabId === tabId) {
      await cancelCandidate("The chart tab navigated or reloaded before selection completed.", status);
    }
    if (status.lockedTabId !== tabId) return;
    const originChanged = changeInfo.url && sourceOrigin(tab.url) !== status.lockedOrigin;
    if (originChanged || changeInfo.status === "loading") {
      await stopCapture("The locked chart tab navigated or reloaded; select its chart region again.");
    }
  })();
});

chrome.tabCapture.onStatusChanged.addListener((info) => {
  void (async () => {
    if (!["stopped", "error"].includes(info.status)) return;
    const status = await loadStatus();
    if (status.lockedTabId === info.tabId) {
      await stopCapture(`Edge reported the locked chart capture as ${info.status}.`);
    } else if (status.candidateTabId === info.tabId) {
      await cancelCandidate(`Edge reported the candidate chart capture as ${info.status}.`, status);
    }
  })();
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.target === "offscreen") return false;
  if (message?.type === "OFFSCREEN_STATUS_V2") {
    if (sender?.url !== OFFSCREEN_URL || sender?.tab) return false;
    void publishStatus(message.status || {}).then(() => sendResponse({ok: true}));
    return true;
  }
  if (message?.type === "OFFSCREEN_CANDIDATE_TIMEOUT_V1") {
    if (sender?.url !== OFFSCREEN_URL || sender?.tab) return false;
    void (async () => {
      const status = await loadStatus();
      if (!status.selectionId || status.selectionId !== message.selectionId || status.candidateTabId !== Number(message.candidateTabId || 0)) {
        return {ok: false, error: "The timed-out candidate is no longer current."};
      }
      await dismissSelector(status);
      const next = await cancelCandidate("Chart-region selection timed out after 60 seconds.", status);
      return {ok: true, status: next};
    })().then(sendResponse);
    return true;
  }
  if (message?.type === "ROI_SELECTION_CONFIRMED_V1") {
    void commitSelection(message, sender).then(sendResponse);
    return true;
  }
  if (message?.type === "ROI_SELECTION_CANCELLED_V1") {
    void (async () => {
      const status = await loadStatus();
      const trusted = sender?.id === chrome.runtime.id && Number(sender?.frameId || 0) === 0 &&
        Number(sender?.tab?.id || 0) === status.candidateTabId && message.selectionId === status.selectionId;
      if (!trusted) return {ok: false, error: "Untrusted or expired selector cancellation."};
      const next = await cancelCandidate(message.reason || "Selection cancelled by the operator.", status);
      return {ok: true, status: next};
    })().then(sendResponse);
    return true;
  }
  if (message?.type === "GET_EXTENSION_STATUS") {
    void Promise.all([loadStatus(), loadConfig(), chrome.commands.getAll()]).then(([status, config, commands]) => {
      sendResponse({ok: true, status, commands, config: {...config, token: "", signingSecret: ""}});
    });
    return true;
  }
  if (message?.type === "STOP_CAPTURE_REQUEST") {
    void stopCapture("Capture stopped from extension options.").then((status) => sendResponse({ok: true, status}));
    return true;
  }
  return false;
});

void syncFromOffscreen();
