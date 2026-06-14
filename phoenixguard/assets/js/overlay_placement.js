// Shared overlay label placement utilities
(function(){
  const layerWeights = {
    broker_controls: 5,
    active_council_decision: 4,
    trigger_zones: 3,
    target_zones: 3,
    invalidation: 3,
    prediction_path: 2.5,
    major_swings: 2.5,
    supply_demand: 2,
    local_swings: 2,
    recent_candles: 1.5,
    historical_replay: 1,
    diagnostics: 0.5,
  };

  function computePriority(box){
    try{
      const conf = Number(box.confidence || 0) || 0;
      const visDef = box.visible_default === true ? 1 : 0;
      const lw = Number(layerWeights[box.layer || box.type] || 0);
      return Math.round((conf * 100 + visDef * 50 + lw * 10) * 100) / 100;
    }catch(e){ return 0; }
  }

  // common nudge/attempt offsets (x,y) in pixels
  const defaultAttempts = [ [0,0], [10,0], [-10,0], [0,-18], [0,18], [20,0], [-20,0], [30,0], [-30,0] ];

  window.OverlayPlacement = {
    computePriority,
    attempts: defaultAttempts,
    layerWeights,
  };
})();
