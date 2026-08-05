(() => {
  if (globalThis.__phoenixGuardRegionSelectorV1) return;

  const ROOT_ID = "phoenixguard-roi-selector-root";
  const MIN_WIDTH = 320;
  const MIN_HEIGHT = 180;
  let active = null;

  function nextPaint() {
    return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }

  function viewportSnapshot() {
    const visual = window.visualViewport;
    return {
      viewportCss: {width: window.innerWidth, height: window.innerHeight},
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

  function removeSelector() {
    if (!active) return;
    window.removeEventListener("keydown", active.onKeyDown, true);
    window.removeEventListener("resize", active.onResize, true);
    active.root.remove();
    active = null;
  }

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, Number(value) || 0));
  }

  function openSelector(message) {
    removeSelector();
    const selectionId = String(message.selectionId || "");
    if (!selectionId) return {ok: false, error: "Missing selector identity."};

    const root = document.createElement("div");
    root.id = ROOT_ID;
    root.tabIndex = -1;
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-label", "Select the chart region PhoenixGuard should study");
    root.innerHTML = `
      <div class="pg-roi-instructions">
        <strong>Select the exact chart area</strong>
        <span>Drag over candles and price geometry. Minimum ${MIN_WIDTH} × ${MIN_HEIGHT}px.</span>
        <span class="pg-roi-keys">Enter confirms · F selects full viewport · Esc cancels · R resets</span>
      </div>
      <div class="pg-roi-box" hidden><span class="pg-roi-size"></span></div>
      <div class="pg-roi-actions">
        <button type="button" data-action="full">Full viewport</button>
        <button type="button" data-action="reset">Reset</button>
        <button type="button" data-action="cancel">Cancel</button>
        <button type="button" data-action="confirm" class="pg-roi-confirm" disabled>Use this chart region</button>
      </div>
      <div class="pg-roi-error" role="status" aria-live="polite"></div>
    `;
    document.documentElement.appendChild(root);

    const box = root.querySelector(".pg-roi-box");
    const size = root.querySelector(".pg-roi-size");
    const error = root.querySelector(".pg-roi-error");
    const confirm = root.querySelector('[data-action="confirm"]');
    let pointerId = null;
    let anchor = null;
    let rect = null;

    function render() {
      if (!rect) {
        box.hidden = true;
        confirm.disabled = true;
        error.textContent = "";
        return;
      }
      box.hidden = false;
      box.style.left = `${rect.x}px`;
      box.style.top = `${rect.y}px`;
      box.style.width = `${rect.width}px`;
      box.style.height = `${rect.height}px`;
      size.textContent = `${Math.round(rect.width)} × ${Math.round(rect.height)}`;
      const usable = rect.width >= MIN_WIDTH && rect.height >= MIN_HEIGHT;
      confirm.disabled = !usable;
      error.textContent = usable ? "" : `Make the region at least ${MIN_WIDTH} × ${MIN_HEIGHT}px.`;
    }

    function setRect(x1, y1, x2, y2) {
      const width = window.innerWidth;
      const height = window.innerHeight;
      const left = clamp(Math.min(x1, x2), 0, width);
      const top = clamp(Math.min(y1, y2), 0, height);
      const right = clamp(Math.max(x1, x2), 0, width);
      const bottom = clamp(Math.max(y1, y2), 0, height);
      rect = {x: left, y: top, width: right - left, height: bottom - top};
      render();
    }

    async function cancel(reason = "Selection cancelled by the operator.") {
      removeSelector();
      await nextPaint();
      await chrome.runtime.sendMessage({
        type: "ROI_SELECTION_CANCELLED_V1",
        selectionId,
        reason
      }).catch(() => undefined);
    }

    async function commit() {
      if (!rect || rect.width < MIN_WIDTH || rect.height < MIN_HEIGHT) return;
      const payload = {...viewportSnapshot(), rectCss: {...rect}};
      removeSelector();
      await nextPaint();
      await chrome.runtime.sendMessage({
        type: "ROI_SELECTION_CONFIRMED_V1",
        selectionId,
        selection: payload
      }).catch(() => undefined);
    }

    function onKeyDown(event) {
      if (!event.isTrusted) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        void cancel();
      } else if (event.key === "Enter" && !confirm.disabled) {
        event.preventDefault();
        event.stopImmediatePropagation();
        void commit();
      } else if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        event.stopImmediatePropagation();
        setRect(0, 0, window.innerWidth, window.innerHeight);
      } else if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        event.stopImmediatePropagation();
        rect = null;
        render();
      }
    }

    function onResize() {
      if (!rect) return;
      setRect(rect.x, rect.y, Math.min(rect.x + rect.width, window.innerWidth), Math.min(rect.y + rect.height, window.innerHeight));
    }

    root.addEventListener("pointerdown", (event) => {
      if (!event.isTrusted || event.button !== 0 || event.target.closest("button")) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      pointerId = event.pointerId;
      anchor = {x: event.clientX, y: event.clientY};
      root.setPointerCapture(pointerId);
      setRect(anchor.x, anchor.y, anchor.x, anchor.y);
    }, true);

    root.addEventListener("pointermove", (event) => {
      if (!event.isTrusted || pointerId !== event.pointerId || !anchor) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      setRect(anchor.x, anchor.y, event.clientX, event.clientY);
    }, true);

    root.addEventListener("pointerup", (event) => {
      if (!event.isTrusted || pointerId !== event.pointerId || !anchor) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      setRect(anchor.x, anchor.y, event.clientX, event.clientY);
      root.releasePointerCapture(pointerId);
      pointerId = null;
      anchor = null;
    }, true);

    root.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);

    root.querySelector('[data-action="full"]').addEventListener("click", (event) => {
      if (!event.isTrusted) return;
      setRect(0, 0, window.innerWidth, window.innerHeight);
    });
    root.querySelector('[data-action="reset"]').addEventListener("click", (event) => {
      if (!event.isTrusted) return;
      rect = null;
      render();
    });
    root.querySelector('[data-action="cancel"]').addEventListener("click", (event) => {
      if (event.isTrusted) void cancel();
    });
    confirm.addEventListener("click", (event) => {
      if (event.isTrusted) void commit();
    });

    active = {root, selectionId, onKeyDown, onResize};
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("resize", onResize, true);
    root.focus({preventScroll: true});
    return {ok: true};
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "OPEN_ROI_SELECTOR_V1") {
      sendResponse(openSelector(message));
      return false;
    }
    if (message?.type === "DISMISS_ROI_SELECTOR_V1") {
      if (!active || !message.selectionId || active.selectionId === message.selectionId) removeSelector();
      sendResponse({ok: true});
      return false;
    }
    return false;
  });

  globalThis.__phoenixGuardRegionSelectorV1 = {openSelector, removeSelector};
})();
