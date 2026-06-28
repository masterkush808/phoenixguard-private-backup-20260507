// ES module: overlay placement helpers (priority, attempts, collision resolver)
export const layerWeights = {
  broker_controls: 5,
  active_council_decision: 4,
  trigger_zones: 3,
  target_zones: 3,
  invalidation: 3,
  prediction_path: 2.5,
  major_swings: 2.5,
  trendlines: 2.4,
  supply_demand: 2,
  local_swings: 2,
  recent_candles: 1.5,
  historical_replay: 1,
  diagnostics: 0.5,
};

export function computePriority(box){
  try{
    const style = box && typeof box.style === 'object' ? box.style : {};
    const conf = Number(box.overlay_confidence || box.confidence || box.score || style.confidence || 0) || 0;
    const visDef = box.visible_default === true ? 1 : 0;
    const lw = Number(layerWeights[box.layer || box.type] || 0);
    const visualWeight = Number(box.visual_weight || style.visual_weight || 0) || 0;
    return Math.round((conf * 100 + visDef * 50 + lw * 10 + visualWeight * 10) * 100) / 100;
  }catch(e){ return 0; }
}

export const attempts = [ [0,0], [10,0], [-10,0], [0,-18], [0,18], [20,0], [-20,0], [30,0], [-30,0] ];

export function resolveLabelCollisions(container){
  try{
    const root = container || document.querySelector('.hotspot-layer');
    if(!root) return;
    const children = Array.from(root.querySelectorAll('button.surface-hotspot'));
    children.sort((a,b)=> Number(b.dataset.priority||0) - Number(a.dataset.priority||0));
    const keptRects = [];
    const rectsIntersect = (a,b)=>!(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);
    for(const node of children){
      const span = node.querySelector('span');
      if(!span) continue;
      node.hidden = false;
      node.style.display = '';
      node.classList.remove('label-hidden');
      const r = span.getBoundingClientRect();
      let overlap = false;
      for(const k of keptRects){ if(rectsIntersect(r,k)){ overlap = true; break; } }
      if(overlap){ node.classList.add('label-hidden'); } else { keptRects.push(r); }
    }
  }catch(e){ /* ignore */ }
}
