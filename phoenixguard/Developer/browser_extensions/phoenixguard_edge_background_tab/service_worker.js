import {
  DEFAULT_CONFIG,
  SELECTION_TIMEOUT_MS,
  captureDiscardPolicy,
  captureRegistryAttestation,
  initialStatus,
  lockedTabLifecycleAction,
  makeSequenceId,
  normalizeConfig,
  normalizeRegionSelection,
  sanitizeSourceUrl,
  sourceOrigin,
  tabCaptureLineageStillCurrent,
  terminalTabCaptureTarget,
  validateCapturableTab,
  validateAuthorizedBackgroundTab,
  validateConfig
} from "./common.js";

const CONFIG_KEY = "captureConfig";
const STATUS_KEY = "captureStatus";
const AUTHORIZED_BINDING_KEY = "authorizedCaptureBindingV1";
const RECOVERY_ALARM = "phoenixguard-recover-authorized-capture";
const OFFSCREEN_PATH = "offscreen.html";
const OFFSCREEN_URL = chrome.runtime.getURL(OFFSCREEN_PATH);
const EXTENSION_VERSION = String(chrome.runtime.getManifest()?.version || "unknown");

let creatingOffscreenDocument = null;
let stoppingCapture = null;
let startingSelection = null;
let selectionTimeout = null;
let recoveryInFlight = null;
let recoveryRetryTimer = null;

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
  const raw = stored[CONFIG_KEY] || DEFAULT_CONFIG;
  const normalized = normalizeConfig(raw);
  if (String(raw.symbol || "").trim() || String(raw.timeframe || "").trim()) {
    // One-time migration for installations that previously persisted a pair
    // or timeframe. A new ROI must always establish its identity from pixels.
    await chrome.storage.local.set({[CONFIG_KEY]: normalized});
  }
  return normalized;
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

function normalizeAuthorizedBinding(raw = {}) {
  const tabId = Number(raw.tabId || 0);
  const origin = sourceOrigin(raw.url || raw.origin || "");
  const normalized = normalizeRegionSelection(raw.region || {});
  if (!Number.isInteger(tabId) || tabId <= 0 || !origin || !normalized.ok) return null;
  return {
    schemaVersion: "PG_EDGE_AUTHORIZED_CAPTURE_BINDING_V1",
    tabId,
    title: String(raw.title || "Selected Edge chart").slice(0, 180),
    url: sanitizeSourceUrl(raw.url).slice(0, 2048),
    origin,
    region: normalized.region,
    originalAutoDiscardable: raw.originalAutoDiscardable !== false,
    authorizedAt: String(raw.authorizedAt || new Date().toISOString())
  };
}

async function loadAuthorizedBinding() {
  const stored = await chrome.storage.local.get(AUTHORIZED_BINDING_KEY);
  return normalizeAuthorizedBinding(stored[AUTHORIZED_BINDING_KEY] || {});
}

async function saveAuthorizedBinding(status, region) {
  const binding = normalizeAuthorizedBinding({
    tabId: status?.candidateTabId || status?.lockedTabId,
    title: status?.candidateTitle || status?.lockedTitle,
    url: status?.candidateUrl || status?.lockedUrl,
    origin: status?.candidateOrigin || status?.lockedOrigin,
    region,
    originalAutoDiscardable: status?.candidateTabOriginalAutoDiscardable !== false,
    authorizedAt: new Date().toISOString()
  });
  if (!binding) throw new Error("The committed chart binding could not be made restart-safe.");
  await chrome.storage.local.set({[AUTHORIZED_BINDING_KEY]: binding});
  return binding;
}

async function clearAuthorizedBinding() {
  await chrome.storage.local.remove(AUTHORIZED_BINDING_KEY);
}

function cancelRecoverySchedule() {
  if (recoveryRetryTimer) clearTimeout(recoveryRetryTimer);
  recoveryRetryTimer = null;
  void chrome.alarms.clear(RECOVERY_ALARM);
}

function scheduleAuthorizedRecovery(delayMs = 4000) {
  const delay = Math.max(1000, Number(delayMs) || 4000);
  if (recoveryRetryTimer) clearTimeout(recoveryRetryTimer);
  recoveryRetryTimer = setTimeout(() => {
    recoveryRetryTimer = null;
    void recoverAuthorizedCapture("Retrying the already-authorized chart capture after a temporary interruption.");
  }, delay);
  void chrome.alarms.create(RECOVERY_ALARM, {when: Date.now() + Math.max(delay, 30_000)});
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

async function protectTabFromDiscard(tabId, status = null) {
  const id = Number(tabId || 0);
  if (id <= 0) return {active: false, originalAutoDiscardable: true};
  if (status?.discardProtectionActive && Number(status.lockedTabId || 0) === id) {
    return {
      active: true,
      originalAutoDiscardable: status.lockedTabOriginalAutoDiscardable !== false
    };
  }
  const tab = await chrome.tabs.get(id);
  const policy = captureDiscardPolicy(tab.autoDiscardable);
  if (policy.protectionUpdate) {
    await chrome.tabs.update(id, policy.protectionUpdate);
  }
  return {active: true, originalAutoDiscardable: policy.originalAutoDiscardable};
}

async function restoreTabDiscardPolicy(tabId, originalAutoDiscardable) {
  const id = Number(tabId || 0);
  const policy = captureDiscardPolicy(originalAutoDiscardable);
  if (id <= 0 || !policy.restorationUpdate) return;
  try {
    await chrome.tabs.update(id, policy.restorationUpdate);
  } catch {
    // Closing or replacing a tab removes its discard policy with the tab.
  }
}

async function releaseCandidateDiscardProtection(status) {
  const candidateTabId = Number(status?.candidateTabId || 0);
  if (candidateTabId <= 0 || candidateTabId === Number(status?.lockedTabId || 0)) return;
  await restoreTabDiscardPolicy(candidateTabId, status?.candidateTabOriginalAutoDiscardable);
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
  cancelRecoverySchedule();
  await clearAuthorizedBinding();
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
  await Promise.all([
    restoreTabDiscardPolicy(previous.lockedTabId, previous.lockedTabOriginalAutoDiscardable),
    releaseCandidateDiscardProtection(previous)
  ]);
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
    discardProtectionActive: false,
    lockedTabOriginalAutoDiscardable: true,
    candidateTabOriginalAutoDiscardable: true,
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
  await releaseCandidateDiscardProtection(status);
  if (response?.status) {
    return publishStatus({
      ...response.status,
      candidateTabOriginalAutoDiscardable: true
    });
  }
  return publishStatus({
    phase: status.lockedTabId > 0 ? status.phase === "selecting" ? "live" : status.phase : "stopped",
    message: status.lockedTabId > 0 ? "Existing chart capture preserved; region selection was cancelled." : reason,
    selectionId: "",
    candidateTabId: 0,
    candidateTitle: "",
    candidateUrl: "",
    candidateOrigin: "",
    candidateTabOriginalAutoDiscardable: true
  });
}

async function injectSelector(tabId) {
  await chrome.scripting.insertCSS({target: {tabId, frameIds: [0]}, files: ["roi_selector.css"]});
  await chrome.scripting.executeScript({target: {tabId, frameIds: [0]}, files: ["roi_selector.js"]});
}

async function queryFullViewportSelection(tabId) {
  const results = await chrome.scripting.executeScript({
    target: {tabId, frameIds: [0]},
    func: () => {
      const visual = window.visualViewport;
      return {
        viewportCss: {width: window.innerWidth, height: window.innerHeight},
        rectCss: {x: 0, y: 0, width: window.innerWidth, height: window.innerHeight},
        devicePixelRatio: window.devicePixelRatio || 1,
        visualViewport: {
          offsetLeft: visual?.offsetLeft || 0,
          offsetTop: visual?.offsetTop || 0,
          width: visual?.width || window.innerWidth,
          height: visual?.height || window.innerHeight,
          scale: visual?.scale || 1
        }
      };
    }
  });
  const selection = results?.[0]?.result || null;
  const normalized = normalizeRegionSelection(selection || {});
  if (!normalized.ok) throw new Error(`The active tab has no usable full viewport: ${normalized.reason}`);
  return selection;
}

async function beginRegionSelectionOnce(tab, selectionMode = "interactive_roi") {
  cancelRecoverySchedule();
  const fullViewport = selectionMode === "full_viewport";
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
  let discardProtection = null;
  let sameTabReselection = false;

  try {
    discardProtection = await protectTabFromDiscard(tab.id, previous);
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
    if (!fullViewport) await injectSelector(tab.id);
    sameTabReselection = Number(liveOffscreenStatus?.lockedTabId || 0) === tab.id &&
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
      extensionVersion: EXTENSION_VERSION,
      config: configValidation.config,
      timeoutMs: SELECTION_TIMEOUT_MS
    });
    if (!prepared?.ok) throw new Error(prepared?.error || "The offscreen document rejected the provisional chart stream.");

    const nextStatus = await publishStatus({
      ...(prepared.status || {}),
      phase: fullViewport ? "locking" : "selecting",
      message: fullViewport
        ? "Locking the active tab's full viewport from an explicit browser command."
        : "Drag over the exact chart region, then confirm it. No selector pixels are uploaded.",
      selectionId,
      candidateTabId: candidate.id,
      candidateTitle: candidate.title,
      candidateUrl: candidate.url,
      candidateOrigin: candidate.origin,
      candidateTabOriginalAutoDiscardable: discardProtection.originalAutoDiscardable,
      lastError: ""
    });
    if (fullViewport) {
      const selection = await queryFullViewportSelection(tab.id);
      const currentTab = await chrome.tabs.get(tab.id);
      const currentStatus = await loadStatus();
      if (
        currentStatus.selectionId !== selectionId ||
        Number(currentStatus.candidateTabId || 0) !== Number(tab.id)
      ) {
        throw new Error("The full-viewport candidate was replaced before it could be committed.");
      }
      if (sourceOrigin(currentTab?.url) !== candidate.origin) {
        throw new Error("The active chart tab changed origin before its full viewport was committed.");
      }
      const committed = await commitCandidateRegion(currentStatus, selection, {dismissSelectorUi: false});
      if (!committed.ok) throw new Error(committed.error || "The full viewport was not committed.");
      return {ok: true, status: await loadStatus()};
    }
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
    if (discardProtection?.active && !sameTabReselection && Number(previous.lockedTabId || 0) !== candidate.id) {
      await restoreTabDiscardPolicy(candidate.id, discardProtection.originalAutoDiscardable);
    }
    if (Number(recovered?.lockedTabId || 0) > 0) {
      await publishStatus({lastError: `New selection failed while the existing chart remained locked: ${message}`});
    } else {
      await publishStatus({phase: "error", message: `Chart-region selection failed: ${message}`, lastError: message});
    }
    return {ok: false, error: message};
  }
}

async function beginSelection(tab, selectionMode) {
  if (!startingSelection) {
    startingSelection = beginRegionSelectionOnce(tab, selectionMode).finally(() => {
      startingSelection = null;
    });
  }
  return startingSelection;
}

function beginRegionSelection(tab) {
  return beginSelection(tab, "interactive_roi");
}

function beginFullViewportSelection(tab) {
  return beginSelection(tab, "full_viewport");
}

async function resolveCommandTab(tab) {
  if (tab && Number.isInteger(tab.id) && tab.id > 0) return tab;
  const candidates = await chrome.tabs.query({active: true, lastFocusedWindow: true});
  return candidates[0] || null;
}

async function commitCandidateRegion(status, selection, {dismissSelectorUi = true} = {}) {
  const normalized = normalizeRegionSelection(selection);
  if (!normalized.ok) return {ok: false, error: normalized.reason};
  if (dismissSelectorUi) await dismissSelector(status);
  if (selectionTimeout) clearTimeout(selectionTimeout);
  selectionTimeout = null;

  try {
    const response = await sendToOffscreen({
      type: "COMMIT_CAPTURE_REGION_V1",
      selectionId: status.selectionId,
      region: normalized.region
    });
    if (!response?.ok) throw new Error(response?.error || "The selected chart region was not committed.");
    if (Number(status.lockedTabId || 0) > 0 && Number(status.lockedTabId) !== Number(status.candidateTabId)) {
      await restoreTabDiscardPolicy(status.lockedTabId, status.lockedTabOriginalAutoDiscardable);
    }
    if (response.status) {
      await publishStatus({
        ...response.status,
        discardProtectionActive: true,
        lockedTabOriginalAutoDiscardable: status.candidateTabOriginalAutoDiscardable !== false,
        candidateTabOriginalAutoDiscardable: true
      });
    }
    await saveAuthorizedBinding(status, normalized.region);
    cancelRecoverySchedule();
    return {ok: true};
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error);
    await cancelCandidate(`Chart-region commit failed: ${text}`, status);
    return {ok: false, error: text};
  }
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
  return commitCandidateRegion(status, message.selection);
}

async function recoverAuthorizedCaptureOnce(reason) {
  if (stoppingCapture || startingSelection) return false;
  const binding = await loadAuthorizedBinding();
  if (!binding) {
    cancelRecoverySchedule();
    return false;
  }

  let tab;
  try {
    tab = await chrome.tabs.get(binding.tabId);
  } catch {
    await clearAuthorizedBinding();
    cancelRecoverySchedule();
    return false;
  }
  const validation = validateAuthorizedBackgroundTab(tab, binding.origin);
  if (!validation.ok) {
    await clearAuthorizedBinding();
    cancelRecoverySchedule();
    await publishStatus({
      phase: "stopped",
      message: "The authorized chart tab closed or changed origin; select a chart again.",
      lockedTabId: 0,
      candidateTabId: 0,
      selectionId: "",
      sourceRenderFresh: false,
      lastError: validation.reason
    });
    return false;
  }

  const configValidation = validateConfig(await loadConfig());
  if (!configValidation.ok) {
    await publishStatus({
      phase: "configuration_required",
      message: `${configValidation.reason} PhoenixGuard will retry without opening or focusing a tab.`,
      lastError: configValidation.reason
    });
    return false;
  }

  const selectionId = crypto.randomUUID();
  const sequenceId = makeSequenceId(tab.id);
  const discardProtection = await protectTabFromDiscard(tab.id);
  await ensureOffscreenDocument();
  const streamId = await chrome.tabCapture.getMediaStreamId({targetTabId: tab.id});
  const prepared = await sendToOffscreen({
    type: "PREPARE_CAPTURE_CANDIDATE_V1",
    streamId,
    reuseActiveStream: false,
    tab: {
      id: tab.id,
      title: String(tab.title || binding.title).slice(0, 180),
      url: sanitizeSourceUrl(tab.url).slice(0, 2048),
      origin: binding.origin
    },
    selectionId,
    sequenceId,
    extensionVersion: EXTENSION_VERSION,
    config: configValidation.config,
    timeoutMs: SELECTION_TIMEOUT_MS
  });
  if (!prepared?.ok) throw new Error(prepared?.error || "The offscreen document rejected automatic capture recovery.");

  const recoveryStatus = await publishStatus({
    ...(prepared.status || {}),
    phase: "starting",
    message: reason,
    selectionId,
    candidateTabId: tab.id,
    candidateTitle: String(tab.title || binding.title).slice(0, 180),
    candidateUrl: sanitizeSourceUrl(tab.url).slice(0, 2048),
    candidateOrigin: binding.origin,
    candidateTabOriginalAutoDiscardable: discardProtection.originalAutoDiscardable,
    lastError: ""
  });
  const committed = await commitCandidateRegion(
    recoveryStatus,
    binding.region,
    {dismissSelectorUi: false}
  );
  if (!committed.ok) throw new Error(committed.error || "The authorized chart capture could not be recommitted.");
  await publishStatus({
    phase: "starting",
    message: "The existing chart capture recovered in the background without opening, activating, or focusing a tab.",
    lastError: ""
  });
  cancelRecoverySchedule();
  return true;
}

async function recoverAuthorizedCapture(reason = "Recovering the already-authorized chart capture in the background.") {
  if (!recoveryInFlight) {
    recoveryInFlight = recoverAuthorizedCaptureOnce(reason)
      .catch(async (error) => {
        await publishStatus({
          phase: "waiting_for_ingest",
          message: "The authorized chart remains locked while PhoenixGuard reconnects in the background.",
          sourceRenderFresh: false,
          lastError: error instanceof Error ? error.message : String(error)
        });
        return false;
      })
      .finally(() => {
        recoveryInFlight = null;
      });
  }
  const recovered = Boolean(await recoveryInFlight);
  if (!recovered && await loadAuthorizedBinding()) scheduleAuthorizedRecovery(4000);
  return recovered;
}

async function syncFromOffscreen() {
  if (!(await offscreenDocumentExists())) {
    const status = await loadStatus();
    const binding = await loadAuthorizedBinding();
    if (binding) {
      await publishStatus({
        phase: "starting",
        message: "Restoring the already-authorized chart capture in the background.",
        sourceRenderFresh: false,
        lastError: ""
      });
      await recoverAuthorizedCapture();
    } else if (status.lockedTabId > 0 || status.candidateTabId > 0) {
      await Promise.all([
        restoreTabDiscardPolicy(status.lockedTabId, status.lockedTabOriginalAutoDiscardable),
        releaseCandidateDiscardProtection(status)
      ]);
      await publishStatus({
        phase: "stopped",
        message: "The previous chart stream ended while Edge was not running.",
        lockedTabId: 0,
        candidateTabId: 0,
        selectionId: "",
        sourceRenderFresh: false,
        discardProtectionActive: false,
        lockedTabOriginalAutoDiscardable: true,
        candidateTabOriginalAutoDiscardable: true
      });
    } else {
      await setActionStatus(status);
    }
    return;
  }
  try {
    const response = await sendToOffscreen({type: "GET_STATUS"});
    if (response?.status) {
      await publishStatus(response.status);
      if (String(response.status.phase || "").toLowerCase() === "stopped" && await loadAuthorizedBinding()) {
        await recoverAuthorizedCapture();
      }
    }
  } catch (error) {
    console.debug("Unable to restore offscreen status.", error);
  }
}

async function inspectLockedCapture(message = {}) {
  const status = await loadStatus();
  const tabId = Number(message.tabId || 0);
  const lineage = captureRegistryAttestation(status, message, {}, [], Date.now());
  if (!lineage.ok) return lineage;

  try {
    const [tab, capturedTabs] = await Promise.all([
      chrome.tabs.get(tabId),
      chrome.tabCapture.getCapturedTabs()
    ]);
    return captureRegistryAttestation(status, message, tab, capturedTabs, Date.now());
  } catch (error) {
    return {
      ok: false,
      captureStatus: "unknown",
      checkedAtMs: Date.now(),
      error: error instanceof Error ? error.message : String(error)
    };
  }
}

async function observeLockedTabIdentity(message = {}) {
  const status = await loadStatus();
  const tabId = Number(message.tabId || 0);
  const sequenceId = String(message.sequenceId || "");
  if (
    tabId <= 0 ||
    tabId !== Number(status.lockedTabId || 0) ||
    !sequenceId ||
    sequenceId !== String(status.sequenceId || "")
  ) {
    return {ok: false, error: "The requested identity observation is not the locked chart lineage."};
  }
  try {
    const tab = await chrome.tabs.get(tabId);
    if (sourceOrigin(tab?.url) !== status.lockedOrigin) {
      return {ok: false, error: "The locked chart origin changed before identity observation."};
    }
    const results = await chrome.scripting.executeScript({
      target: {tabId, frameIds: [0]},
      func: () => {
        const viewport = {width: window.innerWidth, height: window.innerHeight};
        const visible = (element) => {
          const rect = element.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0) return null;
          if (rect.right <= 0 || rect.bottom <= 0 || rect.left >= viewport.width || rect.top >= viewport.height) return null;
          const style = window.getComputedStyle(element);
          if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) <= 0) return null;
          return rect;
        };
        const textOf = (element) => String(element.textContent || element.innerText || "").replace(/\s+/g, " ").trim();
        const bbox = (rect) => [rect.left, rect.top, rect.right, rect.bottom].map((value) => Number(value.toFixed(2)));
        const interactive = (element) => {
          const role = String(element.getAttribute("role") || "").toLowerCase();
          return ["BUTTON", "SELECT", "INPUT", "A"].includes(element.tagName) ||
            ["button", "combobox", "listbox", "option", "tab"].includes(role) ||
            element.hasAttribute("aria-expanded") ||
            element.tabIndex >= 0;
        };
        const elements = Array.from(document.querySelectorAll(
          "button,[role='button'],[role='combobox'],[role='listbox'],[role='tab'],select,input,span"
        )).slice(0, 6000);
        const symbols = [];
        for (const element of elements) {
          const text = textOf(element).toUpperCase();
          const match = text.match(/^([A-Z]{3}\/[A-Z]{3})(?:\s+(OTC))?$/);
          if (!match) continue;
          const rect = visible(element);
          if (!rect || rect.width > 360 || rect.height > 100) continue;
          const normalized = `${match[1]}${match[2] ? " OTC" : ""}`;
          let score = interactive(element) ? 100 : 0;
          if (element.hasAttribute("aria-expanded")) score += 60;
          if (rect.left < viewport.width * 0.5) score += 30;
          if (rect.top >= viewport.height * 0.12 && rect.top <= viewport.height * 0.42) score += 40;
          if (rect.top < viewport.height * 0.19) score -= 45;
          score -= Math.min(30, (rect.width * rect.height) / 1200);
          symbols.push({symbol: normalized, rect, score});
        }
        symbols.sort((a, b) => b.score - a.score || (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
        const market = symbols[0] || null;
        const timeframes = [];
        for (const element of elements) {
          const text = textOf(element).toUpperCase();
          if (!/^(?:S\d+|M(?:1|2|3|4|5|10|15|30)|H(?:1|2|3|4|6|8|12)|D1|W1)$/.test(text)) continue;
          const rect = visible(element);
          if (!rect || rect.width > 160 || rect.height > 100) continue;
          let score = interactive(element) ? 70 : 0;
          if (rect.left < viewport.width * 0.55) score += 20;
          if (rect.top >= viewport.height * 0.1 && rect.top <= viewport.height * 0.42) score += 20;
          if (market) {
            const marketX = (market.rect.left + market.rect.right) / 2;
            const marketY = (market.rect.top + market.rect.bottom) / 2;
            const x = (rect.left + rect.right) / 2;
            const y = (rect.top + rect.bottom) / 2;
            const distance = Math.hypot(x - marketX, y - marketY);
            score += Math.max(0, 120 - distance * 0.35);
          }
          timeframes.push({timeframe: text, rect, score});
        }
        timeframes.sort((a, b) => b.score - a.score || (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
        const timeframe = timeframes[0] || null;
        return {
          symbol: market?.symbol || "",
          timeframe: timeframe?.timeframe || "",
          market_bbox_css: market ? bbox(market.rect) : [],
          timeframe_bbox_css: timeframe ? bbox(timeframe.rect) : [],
          viewport_css: viewport,
          observed_epoch: Date.now()
        };
      }
    });
    const observation = results?.[0]?.result || null;
    if (!observation || !observation.symbol || !observation.timeframe) {
      return {ok: false, error: "The chart pair/timeframe controls were not both observed."};
    }
    return {
      ok: true,
      tabId,
      sequenceId,
      origin: status.lockedOrigin,
      observation
    };
  } catch (error) {
    return {ok: false, error: error instanceof Error ? error.message : String(error)};
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get([CONFIG_KEY, STATUS_KEY]);
  await chrome.storage.local.set({
    [CONFIG_KEY]: normalizeConfig(stored[CONFIG_KEY] || DEFAULT_CONFIG)
  });
  if (!stored[STATUS_KEY]) await chrome.storage.local.set({[STATUS_KEY]: initialStatus()});
  await syncFromOffscreen();
});

chrome.runtime.onStartup.addListener(() => void syncFromOffscreen());

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm?.name === RECOVERY_ALARM) {
    void recoverAuthorizedCapture("Retrying the authorized chart capture after PhoenixGuard became available.");
  }
});

chrome.action.onClicked.addListener((tab) => {
  void beginRegionSelection(tab);
});

chrome.commands.onCommand.addListener((command, tab) => {
  if (command === "lock-full-viewport") {
    void resolveCommandTab(tab).then(beginFullViewportSelection);
  } else if (command === "select-chart-region") {
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
  const hasDiscardUpdate = Object.prototype.hasOwnProperty.call(changeInfo, "discarded");
  const hasFrozenUpdate = Object.prototype.hasOwnProperty.call(changeInfo, "frozen");
  if (
    !changeInfo.url &&
    changeInfo.status !== "loading" &&
    !hasDiscardUpdate &&
    !hasFrozenUpdate
  ) return;
  void (async () => {
    const status = await loadStatus();
    if (status.candidateTabId === tabId) {
      await cancelCandidate("The chart tab navigated or reloaded before selection completed.", status);
    }
    if (status.lockedTabId !== tabId) return;
    const lifecycleAction = lockedTabLifecycleAction(changeInfo, tab?.url || status.lockedUrl, status.lockedOrigin);
    if (lifecycleAction === "stop") {
      await stopCapture(changeInfo.discarded === true
        ? "Edge discarded the locked chart tab; select it again after it reloads."
        : "The locked chart tab changed to another origin; select its chart region again.");
      return;
    }
    if (lifecycleAction === "hold") {
      await publishStatus({
        phase: "source_frozen",
        message: "Edge froze the locked chart tab; capture remains attached and will resume without raising the browser.",
        sourceRenderFresh: false,
        lastError: "The locked chart tab is frozen by Edge."
      });
      return;
    }
    if (lifecycleAction === "preserve") {
      await protectTabFromDiscard(tabId, status).catch(() => null);
      try {
        if (await offscreenDocumentExists()) {
          await sendToOffscreen({
            type: "UPDATE_LOCKED_TAB_METADATA_V1",
            tabId,
            sequenceId: status.sequenceId,
            tab: {
              id: tabId,
              title: String(tab?.title || status.lockedTitle || "").slice(0, 180),
              url: sanitizeSourceUrl(tab?.url || status.lockedUrl).slice(0, 2048),
              origin: sourceOrigin(tab?.url || status.lockedUrl)
            }
          });
        }
      } catch (error) {
        console.debug("Unable to refresh same-origin locked-tab metadata.", error);
      }
    }
  })();
});

chrome.tabCapture.onStatusChanged.addListener((info) => {
  void (async () => {
    if (!["stopped", "error"].includes(info.status)) return;
    const status = await loadStatus();
    let capturedTabs = [];
    try {
      capturedTabs = await chrome.tabCapture.getCapturedTabs();
    } catch (error) {
      // A terminal tab event has no stream identity. If Edge cannot confirm
      // that no replacement exists, the offscreen MediaStreamTrack `ended`
      // listener remains the authoritative session-bound teardown signal.
      console.debug("Unable to confirm terminal tab-capture status safely.", error);
      return;
    }
    const target = terminalTabCaptureTarget(info, capturedTabs, status);
    if (!target) return;

    // Storage can advance while getCapturedTabs resolves. Recheck the
    // candidate/active lineage before applying the destructive transition.
    const current = await loadStatus();
    if (target === "locked" && tabCaptureLineageStillCurrent(target, status, current)) {
      if (await loadAuthorizedBinding()) {
        await publishStatus({
          phase: "starting",
          message: `Edge interrupted the chart capture (${info.status}); recovering the same authorized tab and region in the background.`,
          sourceRenderFresh: false,
          lastError: ""
        });
        scheduleAuthorizedRecovery(1000);
      } else {
        await stopCapture(`Edge reported the locked chart capture as ${info.status}.`);
      }
    } else if (target === "candidate" && tabCaptureLineageStillCurrent(target, status, current)) {
      await cancelCandidate(`Edge reported the candidate chart capture as ${info.status}.`, current);
    }
  })();
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.target === "offscreen") return false;
  if (message?.type === "OFFSCREEN_STATUS_V2") {
    if (sender?.url !== OFFSCREEN_URL || sender?.tab) return false;
    void publishStatus(message.status || {}).then(async () => {
      sendResponse({ok: true});
      if (
        String(message.status?.phase || "").toLowerCase() === "stopped" &&
        await loadAuthorizedBinding()
      ) {
        scheduleAuthorizedRecovery(1000);
      }
    });
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
  if (message?.type === "CAPTURE_HEALTH_CHECK_V1") {
    if (sender?.url !== OFFSCREEN_URL || sender?.tab) return false;
    void inspectLockedCapture(message).then(sendResponse);
    return true;
  }
  if (message?.type === "OBSERVE_LOCKED_TAB_IDENTITY_V3") {
    if (sender?.url !== OFFSCREEN_URL || sender?.tab) return false;
    void observeLockedTabIdentity(message).then(sendResponse);
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
