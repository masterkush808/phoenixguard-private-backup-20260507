// Minimal overlay skeleton: polls active overlays and draws on canvas
(function(){
  // Attempt to dynamically import the placement module for better behavior; fall back to window global
  let OverlayPlacementModule = null;
  const API_BASE = (typeof window !== 'undefined' && window.__overlaySkeletonBase)
    ? window.__overlaySkeletonBase
    : ((typeof location !== 'undefined' && location.protocol === 'file:') ? 'http://127.0.0.1:8793' : '');
  function apiPath(path){
    return `${API_BASE}${path}`;
  }
  function resolveUrl(url){
    if(!url) return url;
    if(/^https?:\/\//i.test(url) || /^file:/i.test(url)) return url;
    if(url.startsWith('/')) return apiPath(url);
    return `${API_BASE}/${url}`;
  }
  (async ()=>{
    try{
      OverlayPlacementModule = await import(apiPath('/v1/mobile/window-tracker/assets/js/overlay_placement.esm.js'));
      // also expose globally for legacy consumers
      if (typeof window !== 'undefined') window.OverlayPlacement = OverlayPlacementModule;
    }catch(e){ /* ignore - fallback will use window.OverlayPlacement if present */ }
  })();

  async function fetchActive(sessionId){
    const resp = await fetch(apiPath(`/v1/mobile/live/state/v3/${sessionId}?compact=1`));
    if(!resp.ok) return null;
    const data = await resp.json();
    const overlays = data && typeof data.overlays === 'object' ? data.overlays : {};
    const liveVisualState = data && typeof data.live_visual_state === 'object' ? data.live_visual_state : {};
    const liveVisualOverlays = liveVisualState && typeof liveVisualState.overlays === 'object' ? liveVisualState.overlays : {};
    const activeOverlays = Array.isArray(data && data.active_overlays)
      ? data.active_overlays
      : (Array.isArray(data && data.overlay_objects)
        ? data.overlay_objects
        : (Array.isArray(overlays.objects) ? overlays.objects : (Array.isArray(liveVisualOverlays.objects) ? liveVisualOverlays.objects : [])));
    const chartTransform = data && data.chart_transform
      ? data.chart_transform
      : (overlays.chart_transform || liveVisualState.chart_transform || liveVisualOverlays.chart_transform || null);
    return Object.assign({}, data, {
      active_overlays: activeOverlays,
      chart_transform: chartTransform,
    });
  }
  async function fetchFrame(sessionId){
    try{
      const candidates = [
        apiPath(`/v1/mobile/window-tracker/sessions/${sessionId}/artifacts/latest-window`),
        apiPath(`/v1/mobile/window-tracker/sessions/${sessionId}/artifacts/latest-full-overlay`),
        apiPath(`/v1/mobile/frame/latest.png?session_id=${sessionId}`),
      ];
      for(const candidate of candidates){
        const imgResp = await fetch(candidate);
        if(!imgResp.ok) continue;
        const blob = await imgResp.blob();
        return URL.createObjectURL(blob);
      }
    }catch(e){return null}
    return null;
  }
  function firstText(...values){
    for(const value of values){
      const text = String(value == null ? '' : value).trim();
      if(text) return text;
    }
    return '';
  }
  function normalizedToken(value){
    return firstText(value).toUpperCase().replace(/\s+/g, '');
  }
  function numericFrame(value){
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? Math.floor(number) : 0;
  }
  function payloadSymbol(data){
    const context = data && typeof data.symbol_context === 'object' ? data.symbol_context : {};
    return normalizedToken(data && (data.symbol || data.market || data.display_symbol) || context.symbol || context.display_symbol || context.market);
  }
  function payloadChartTransformId(data, chartTransform){
    return firstText(
      data && (data.chart_transform_id || data.transform_id),
      chartTransform && (chartTransform.chart_transform_id || chartTransform.transform_id || chartTransform.id)
    );
  }
  function payloadFrameId(data, chartTransform){
    return Math.max(
      numericFrame(data && (data.frame_id || data.overlay_object_frame_id || data.overlay_frame_id || data.chart_frame_id)),
      numericFrame(chartTransform && chartTransform.frame_id)
    );
  }
  function overlayMatchesPayload(obj, data, chartTransform){
    if(!obj || !data) return true;
    const expectedFrame = payloadFrameId(data, chartTransform);
    const objectFrame = numericFrame(obj.frame_id || obj.overlay_frame_id || obj.chart_frame_id || obj.source_frame_id);
    if(expectedFrame > 0 && objectFrame > 0 && objectFrame !== expectedFrame) return false;
    const expectedTransform = payloadChartTransformId(data, chartTransform);
    const objectTransform = firstText(obj.chart_transform_id || obj.transform_id);
    if(expectedTransform && objectTransform && objectTransform !== expectedTransform) return false;
    const expectedSymbol = payloadSymbol(data);
    const objectSymbol = normalizedToken(obj.symbol || obj.market || obj.display_symbol || (obj.symbol_context && (obj.symbol_context.symbol || obj.symbol_context.display_symbol)));
    if(expectedSymbol && objectSymbol && objectSymbol !== expectedSymbol) return false;
    return true;
  }
  function overlayDisplayState(obj){
    return normalizedToken(obj && obj.display_state) || 'COMPACT';
  }
  function overlayLabelHidden(obj){
    const state = overlayDisplayState(obj);
    return obj && (
      obj.label_visible === false
      || obj.label_visible === 'false'
      || obj.label_hidden === true
      || obj.label_hidden === 'true'
      || state === 'GHOSTED'
      || state === 'ICON_ONLY'
      || state === 'INSPECTOR_ONLY_LABEL'
    );
  }
  function overlayStyle(obj){
    return obj && typeof obj.style === 'object' ? obj.style : {};
  }
  function draw(overlays, canvas, chartTransform, backgroundImage, payload){
    if(!canvas) return;
    const ctx = canvas.getContext('2d');
    const chartBounds = chartTransform && Array.isArray(chartTransform.chart_image_bounds) ? chartTransform.chart_image_bounds : null;
    const targetWidth = backgroundImage && backgroundImage.naturalWidth
      ? backgroundImage.naturalWidth
      : (chartBounds && Number(chartBounds[2]) > 0 ? Math.round(Number(chartBounds[2])) : canvas.width);
    const targetHeight = backgroundImage && backgroundImage.naturalHeight
      ? backgroundImage.naturalHeight
      : (chartBounds && Number(chartBounds[3]) > 0 ? Math.round(Number(chartBounds[3])) : canvas.height);
    if(canvas.width !== targetWidth || canvas.height !== targetHeight){
      canvas.width = Math.max(1, targetWidth);
      canvas.height = Math.max(1, targetHeight);
    }
    ctx.clearRect(0,0,canvas.width, canvas.height);
    if(backgroundImage && backgroundImage.complete && backgroundImage.naturalWidth > 0){
      ctx.drawImage(backgroundImage, 0, 0, canvas.width, canvas.height);
    }
    const opts = (window.__overlaySkeleton && window.__overlaySkeleton.options) || { tighten: true, labelMode: 'semantic' };
    // Prepare geometries for deterministic label placement using priority and nudging
    const objs = Array.isArray(overlays)
      ? overlays.filter((obj)=>overlayMatchesPayload(obj, payload, chartTransform)).slice(0)
      : [];
    const layerWeights = (OverlayPlacementModule && OverlayPlacementModule.layerWeights) || (window && window.OverlayPlacement && window.OverlayPlacement.layerWeights) || {
      broker_controls: 5, active_council_decision: 4, trigger_zones: 3, target_zones: 3, invalidation: 3, prediction_path: 2.5, major_swings: 2.5,
      supply_demand: 2, local_swings: 2, recent_candles: 1.5, historical_replay: 1, diagnostics: 0.5
    };

    const entries = [];
    for(const obj of objs){
      try{
        let bbox = obj.bbox || obj.normalized_bbox || obj.normalized || [0,0,0,0];
        const isNormalized = Array.isArray(bbox) && bbox.every(v=>typeof v==='number' && v>=0 && v<=1);
        let x1,y1,x2,y2;
        if(isNormalized){
          if(chartTransform && chartTransform.chart_image_bounds){
            const cib = chartTransform.chart_image_bounds;
            const c_w = (cib[2] - (cib[0]||0)) || cib[2] || canvas.width;
            const c_h = (cib[3] - (cib[1]||0)) || cib[3] || canvas.height;
            const cx0 = cib[0] || 0;
            const cy0 = cib[1] || 0;
            const px0 = Math.floor((cx0 + bbox[0] * c_w) * (canvas.width / Math.max(1, c_w)));
            const py0 = Math.floor((cy0 + bbox[1] * c_h) * (canvas.height / Math.max(1, c_h)));
            const px1 = Math.ceil((cx0 + bbox[2] * c_w) * (canvas.width / Math.max(1, c_w)));
            const py1 = Math.ceil((cy0 + bbox[3] * c_h) * (canvas.height / Math.max(1, c_h)));
            x1 = px0; y1 = py0; x2 = px1; y2 = py1;
          }else{
            x1 = Math.floor(bbox[0]*canvas.width);
            y1 = Math.floor(bbox[1]*canvas.height);
            x2 = Math.ceil(bbox[2]*canvas.width);
            y2 = Math.ceil(bbox[3]*canvas.height);
          }
        }else{
          // bbox is provided in chart-image pixel coordinates. Scale to canvas using chartTransform if available.
          if(chartTransform && chartTransform.chart_image_bounds){
            const cib = chartTransform.chart_image_bounds;
            const c_w = (cib[2] - (cib[0]||0)) || cib[2] || canvas.width;
            const c_h = (cib[3] - (cib[1]||0)) || cib[3] || canvas.height;
            const cx0 = cib[0] || 0;
            const cy0 = cib[1] || 0;
            const scaleX = canvas.width / Math.max(1, c_w);
            const scaleY = canvas.height / Math.max(1, c_h);
            x1 = Math.floor(( (bbox[0]||0) - cx0) * scaleX);
            y1 = Math.floor(( (bbox[1]||0) - cy0) * scaleY);
            x2 = Math.ceil(( (bbox[2]||0) - cx0) * scaleX);
            y2 = Math.ceil(( (bbox[3]||0) - cy0) * scaleY);
          }else{
            x1 = Math.floor(bbox[0]||0);
            y1 = Math.floor(bbox[1]||0);
            x2 = Math.ceil(bbox[2]||0);
            y2 = Math.ceil(bbox[3]||0);
          }
        }
        // compute a tighter bbox when requested and available
        function applyTightness(x1,y1,x2,y2){
          try{
            // prefer explicit tight bbox fields if provided
            const t = obj.tight_bbox || obj.tight_normalized;
            if(t && Array.isArray(t) && t.length===4){
              // if normalized (0..1), map to canvas via chart_transform if available
              const isNorm = t.every(v=>typeof v==='number' && v>=0 && v<=1);
              if(isNorm){
                if(chartTransform && chartTransform.chart_image_bounds){
                  const cib = chartTransform.chart_image_bounds;
                  const c_w = (cib[2] - (cib[0]||0)) || cib[2] || canvas.width;
                  const c_h = (cib[3] - (cib[1]||0)) || cib[3] || canvas.height;
                  const cx0 = cib[0] || 0;
                  const cy0 = cib[1] || 0;
                  const scaleX = canvas.width / Math.max(1, c_w);
                  const scaleY = canvas.height / Math.max(1, c_h);
                  const nx1 = Math.floor((cx0 + t[0]*c_w - cx0) * scaleX);
                  const ny1 = Math.floor((cy0 + t[1]*c_h - cy0) * scaleY);
                  const nx2 = Math.ceil((cx0 + t[2]*c_w - cx0) * scaleX);
                  const ny2 = Math.ceil((cy0 + t[3]*c_h - cy0) * scaleY);
                  return [nx1,ny1,nx2,ny2];
                }
                return [Math.floor(t[0]*canvas.width), Math.floor(t[1]*canvas.height), Math.ceil(t[2]*canvas.width), Math.ceil(t[3]*canvas.height)];
              }
              // t provided in absolute chart image pixels; scale if chartTransform present
              if(chartTransform && chartTransform.chart_image_bounds){
                const cib = chartTransform.chart_image_bounds;
                const c_w = (cib[2] - (cib[0]||0)) || cib[2] || canvas.width;
                const c_h = (cib[3] - (cib[1]||0)) || cib[3] || canvas.height;
                const cx0 = cib[0] || 0;
                const cy0 = cib[1] || 0;
                const scaleX = canvas.width / Math.max(1, c_w);
                const scaleY = canvas.height / Math.max(1, c_h);
                const nx1 = Math.floor(((t[0]||0) - cx0) * scaleX);
                const ny1 = Math.floor(((t[1]||0) - cy0) * scaleY);
                const nx2 = Math.ceil(((t[2]||0) - cx0) * scaleX);
                const ny2 = Math.ceil(((t[3]||0) - cy0) * scaleY);
                return [nx1,ny1,nx2,ny2];
              }
              return [Math.floor(t[0]||0), Math.floor(t[1]||0), Math.ceil(t[2]||0), Math.ceil(t[3]||0)];
            }
            if(!opts.tighten) return [x1,y1,x2,y2];
            // shrink the box slightly to make it tighter
            const w = Math.max(1, x2-x1);
            const h = Math.max(1, y2-y1);
            const padX = Math.max(1, Math.floor(w*0.04));
            const padY = Math.max(1, Math.floor(h*0.04));
            return [x1+padX, y1+padY, x2-padX, y2-padY];
          }catch(e){ return [x1,y1,x2,y2]; }
        }
        [x1,y1,x2,y2] = applyTightness(x1,y1,x2,y2);

        // map overlay object to a human-friendly semantic label when requested
        function semanticLabel(o){
          if(!opts.labelMode || opts.labelMode!=='semantic') return null;
          const s = (o.semantic || o.label || o.role || o.type || '').toString().toLowerCase();
          const classHint = (o.cls || o.class || o.category || '').toString().toLowerCase();
          if(o.display_label) return String(o.display_label).toUpperCase();
          if(/support.*trend|trend.*support/.test(s) || /support.*trend|trend.*support/.test(classHint)) return 'SUPPORT TRENDLINE';
          if(/resistance.*trend|trend.*resistance/.test(s) || /resistance.*trend|trend.*resistance/.test(classHint)) return 'RESISTANCE TRENDLINE';
          if(/inner.*trend|micro.*trend|local.*trend/.test(s) || /inner.*trend|micro.*trend|local.*trend/.test(classHint)) return 'INNER TRENDLINE';
          if(/pullback|pull back|pull-back/.test(s) || /pullback|pull back|pull-back/.test(classHint)) return 'PULLBACK';
          if(/continuation|continue/.test(s) || /continuation/.test(classHint)) return 'CONTINUATION';
          if(/rest|resting|pause|consolidat/.test(s) || /rest|pause|consolidat/.test(classHint)) return 'RETEST';
          if(/buy|long/.test(s) || /buy|long/.test(classHint)) return 'SNIPER BUY';
          if(/sell|short/.test(s) || /sell|short/.test(classHint)) return 'SNIPER SELL';
          if(/support/.test(s) || /support/.test(classHint)) return 'SUPPORT';
          if(/resistance/.test(s) || /resist/.test(classHint)) return 'RESISTANCE';
          if(/target|takeprofit|tp/.test(s)) return 'TARGET';
          if(/trigger|entry|reclaim|cancel|invalidate/.test(s)){
            if(/cancel|invalidate/.test(s)) return 'INVALID';
            return 'TRIGGER';
          }
          return 'DEBUG RAW DETECTION';
        }
        let priority = 0;
        try{
          if (OverlayPlacementModule && typeof OverlayPlacementModule.computePriority === 'function'){
            priority = OverlayPlacementModule.computePriority(obj);
          } else if (window && window.OverlayPlacement && typeof window.OverlayPlacement.computePriority === 'function'){
            priority = window.OverlayPlacement.computePriority(obj);
          } else {
            const style = overlayStyle(obj);
            const conf = Number(obj.overlay_confidence || obj.confidence || obj.score || style.confidence || 0) || 0;
            const visDef = obj.visible_default === true ? 1 : 0;
            const lay = obj.layer || obj.type || 'default';
            const lw = Number(layerWeights[lay] || 0);
            const visualWeight = Number(obj.visual_weight || style.visual_weight || 0) || 0;
            priority = conf * 100 + visDef * 50 + lw * 10 + visualWeight * 10;
          }
        }catch(e){ priority = 0; }
        const label = (obj.label && !opts.labelMode) ? String(obj.label) : semanticLabel(obj) || String(obj.id || obj.overlay_id || 'obj');
        entries.push({obj, x1,y1,x2,y2, priority, label});
      }catch(e){/* ignore entry */}
    }

    // Draw all boxes first (consistent visual baseline), style by layer/type
    for(const e of entries){
      try{
        // color palette per layer priority or type
        const layer = (e.obj.layer || e.obj.type || '').toString().toLowerCase();
        let color = 'rgba(255,0,0,0.9)';
        if(/trigger|target/.test(layer)) color = 'rgba(0,200,0,0.95)';
        else if(/supply|demand|support|resist/.test(layer)) color = 'rgba(255,165,0,0.95)';
        else if(/broker_controls|chart_bounds/.test(layer)) color = 'rgba(0,150,255,0.95)';
        else if(/major_swings|local_swings/.test(layer)) color = 'rgba(120,255,120,0.95)';
        const style = overlayStyle(e.obj);
        const displayState = overlayDisplayState(e.obj);
        const requestedOpacity = Number(style.opacity);
        const requestedFillOpacity = Number(style.fill_opacity);
        const opacityFallback = displayState === 'GHOSTED' ? 0.32 : 0.9;
        const opacity = Math.max(0.18, Math.min(1, Number.isFinite(requestedOpacity) ? requestedOpacity : opacityFallback));
        const areaRatio = Math.max(
          0,
          Math.min(
            1,
            ((Math.max(0, e.x2 - e.x1) * Math.max(0, e.y2 - e.y1)) / Math.max(1, canvas.width * canvas.height)),
          ),
        );
        let fillCeiling = displayState === 'GHOSTED' || displayState === 'ICON_ONLY' || displayState === 'INSPECTOR_LABEL' || displayState === 'INSPECTOR_ONLY_LABEL'
          ? 0
          : 0.018;
        if(areaRatio >= 0.18) fillCeiling = 0;
        else if(areaRatio >= 0.10) fillCeiling = Math.min(fillCeiling, 0.003);
        else if(areaRatio >= 0.06) fillCeiling = Math.min(fillCeiling, 0.006);
        else if(areaRatio >= 0.035) fillCeiling = Math.min(fillCeiling, 0.009);
        const fillOpacity = Math.max(0, Math.min(fillCeiling, Number.isFinite(requestedFillOpacity) ? requestedFillOpacity : 0.006));
        ctx.globalAlpha = opacity;
        ctx.strokeStyle = color;
        ctx.lineWidth = Math.max(Number(style.border_width || 0) || 0, Math.max(2, Math.min(4, Math.floor((e.x2-e.x1 + e.y2-e.y1)/150))));
        const evidence = normalizedToken(e.obj.anchor_evidence_status || style.anchor_evidence_status);
        if(evidence === 'STALE' || evidence === 'MISMATCH' || evidence === 'REJECTED') ctx.setLineDash([6, 4]);
        ctx.fillStyle = color.replace(/rgba\(([^,]+),([^,]+),([^,]+),[^)]+\)/, `rgba($1,$2,$3,${fillOpacity})`);
        if(fillOpacity > 0) ctx.fillRect(e.x1,e.y1,Math.max(1,e.x2-e.x1),Math.max(1,e.y2-e.y1));
        ctx.strokeRect(e.x1,e.y1,Math.max(1,e.x2-e.x1),Math.max(1,e.y2-e.y1));
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
      }catch(err){}
    }

    // Then draw labels using priority and nudging heuristics to avoid collisions
    ctx.fillStyle = 'white';
    ctx.font = '13px Inter, Arial, sans-serif';
    const keptRects = [];
    function rectsIntersect(a,b){ return !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom); }
    // Sort descending by priority so we keep higher-priority labels first
    entries.sort((a,b)=>Number(b.priority||0)-Number(a.priority||0));
    for(const e of entries){
      try{
        if(overlayLabelHidden(e.obj)) continue;
        const text = e.label;
        const metrics = ctx.measureText(text);
        const tw = Math.ceil(metrics.width) + 6; // padding
        const th = 14; // approx text height
        // candidate anchors relative to bbox
        const baseX = e.x1 + 4;
        const baseY = e.y1 + 12;
          const attempts = (OverlayPlacementModule && Array.isArray(OverlayPlacementModule.attempts)) ? OverlayPlacementModule.attempts : ((window && window.OverlayPlacement && Array.isArray(window.OverlayPlacement.attempts)) ? window.OverlayPlacement.attempts : [ [0,0], [10,0], [-10,0], [0,-(th+2)], [0,th+2], [20,0], [-20,0], [30,0], [-30,0] ]);
        let placed = false;
        for(const off of attempts){
          const lane = normalizedToken(e.obj.label_lane || e.obj.label_anchor || overlayStyle(e.obj).label_lane || overlayStyle(e.obj).label_anchor);
          const laneOffset = lane === 'BELOW' ? [0, th + 4] : lane === 'RIGHT' ? [Math.max(10, e.x2 - e.x1), 0] : lane === 'LEFT' ? [-tw - 6, 0] : [0, 0];
          const lx = baseX + off[0] + laneOffset[0];
          const ly = baseY + off[1] + laneOffset[1];
          const rect = { left: lx, top: ly-th+2, right: lx+tw, bottom: ly+2 };
          // clamp to canvas
          rect.left = Math.max(0, rect.left);
          rect.top = Math.max(0, rect.top);
          rect.right = Math.min(canvas.width, rect.right);
          rect.bottom = Math.min(canvas.height, rect.bottom);
          let conflict = false;
          for(const k of keptRects){ if(rectsIntersect(rect, k)){ conflict = true; break; } }
          if(!conflict){
              // draw label background for readability and small confidence tag
              ctx.fillStyle = 'rgba(0,0,0,0.65)';
              ctx.fillRect(rect.left-4, rect.top-3, rect.right-rect.left+8, rect.bottom-rect.top+6);
              ctx.fillStyle = 'white';
              ctx.fillText(text, lx, ly);
              if(e.obj.confidence || e.obj.score){
                const conf = (Number(e.obj.confidence || e.obj.score) || 0).toFixed(2);
                ctx.fillStyle = 'rgba(255,255,255,0.75)';
                ctx.font = '10px Inter, Arial, sans-serif';
                ctx.fillText(conf, rect.right - 26, ly);
                ctx.font = '13px Inter, Arial, sans-serif';
                ctx.fillStyle = 'white';
              }
            keptRects.push(rect);
            placed = true;
            break;
          }
        }
        // if not placed, skip label to avoid clutter
      }catch(e){}
    }
  }
  window.__overlaySkeleton = {
    start: async function(sessionId, canvasId){
      const canvas = document.getElementById(canvasId);
      if(!canvas) return;
      let backgroundImage = null;
      fetchFrame(sessionId).then((bgUrl)=>{
        if(!bgUrl) return;
        backgroundImage = new Image();
        backgroundImage.onload = ()=>{
          if(backgroundImage.naturalWidth > 0 && backgroundImage.naturalHeight > 0){
            canvas.width = backgroundImage.naturalWidth;
            canvas.height = backgroundImage.naturalHeight;
          }
          const data = window.__overlaySkeleton && window.__overlaySkeleton._lastData;
          if(data && data.active_overlays){
            draw(data.active_overlays, canvas, data.chart_transform || null, backgroundImage, data);
          }
        };
        backgroundImage.src = bgUrl;
      }).catch(()=>{});
      // initial draw with chart_transform if available
      (async ()=>{
        const data = await fetchActive(sessionId);
        const ct = data && data.chart_transform ? data.chart_transform : null;
        if(data && data.active_overlays){
          window.__overlaySkeleton._lastData = data;
          draw(data.active_overlays, canvas, ct, backgroundImage, data);
        }
      })();
      setInterval(async ()=>{
        const data = await fetchActive(sessionId);
        const ct = data && data.chart_transform ? data.chart_transform : null;
        if(data && data.active_overlays){
          window.__overlaySkeleton._lastData = data;
          draw(data.active_overlays, canvas, ct, backgroundImage, data);
        }
      }, 1000);
    }
  };
})();
