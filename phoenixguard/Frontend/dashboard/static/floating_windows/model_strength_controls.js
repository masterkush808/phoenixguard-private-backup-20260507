(function () {
  "use strict";

  const SCHEMA_VERSION = 2;
  const STORAGE_KEY = "phoenixguard.model.strength.live";
  const CHANNEL_NAME = "phoenixguard.model.strength";

  const DEFAULTS = Object.freeze({
    schemaVersion: SCHEMA_VERSION,
    profileSaved: false,
    panelOpen: true,
    panelLocked: false,
    modelConfidenceFloor: 0.44,
    executionThreshold: 0.70,
    overlayConfidenceFloor: 0.0,
    aiStrengths: {
      market_intelligence: 1.0,
      decision_kernel: 1.0,
      smart_money: 1.0,
      memory_projection: 1.0,
      lstm_sequence: 1.0,
      scenario_engine: 1.0,
      high_frequency: 1.0,
    },
    laneThresholds: {
      HIGH_FREQUENCY_TWO_CANDLE: 0.50,
      SNIPER_ZONE_ENTRY: 0.70,
      FAILED_RETEST_ENTRY: 0.72,
      LOCAL_BREAKDOWN_CONTINUATION: 0.74,
      HISTORY_MATCHED_CONTINUATION: 0.76,
      MOMENTUM_ACCEPTANCE_ENTRY: 0.82,
    },
    timingControls: {
      high_frequency_entry_grace_sec: 45,
      high_frequency_expiry_seconds: 600,
      high_frequency_horizon_candles: 2,
      min_capture_interval_sec: 0.5,
      max_capture_interval_sec: 10,
      adaptive_timer_enabled: true,
      phoenix_report_interval_sec: 20,
    },
    memoryIdentityControls: {
      auto_memory_projection: true,
      require_memory_projection: true,
      live_momentum_memory_advisory: true,
      require_market_identity: true,
      min_market_confidence: 0.42,
      require_timeframe_identity: false,
      min_timeframe_confidence: 0.42,
      broker_surface_cache_sec: 30,
      allow_locked_surface_identity_fallback: false,
    },
    scenarioControls: {
      scenario_generation_enabled: false,
      continuous_model_feed_enabled: true,
      high_frequency_enabled: true,
      two_candle_execution_allowed: false,
      swing_fallback_enabled: false,
      allow_location_sniper_entries: false,
    },
    riskControls: {
      max_executions_per_window: 1,
      execution_window_sec: 600,
      cooldown_sec: 600,
      loss_guard_enabled: true,
      loss_guard_max_consecutive_losses: 2,
      loss_guard_window_sec: 5400,
      loss_guard_pause_sec: 2700,
    },
    entryControls: {
      min_location_sniper_target_candles: 3,
      min_primary_target_candles: 10,
      max_primary_target_candles: 36,
      allow_live_momentum_entries: true,
      min_live_momentum_visible_candles: 8,
      min_live_momentum_score: 0.54,
      min_live_momentum_alignment: 3,
    },
    opposingForceControls: {
      allow_opposing_force_reactions: true,
      min_opposing_force_reaction_score: 0.68,
      min_opposing_force_reaction_alignment: 3,
      min_opposing_force_reaction_risk: 0.72,
      min_opposing_force_reaction_entry_score: 0.54,
      max_opposing_force_reaction_distance: 0.10,
    },
    structureControls: {
      live_max_tracked_candles: 64,
      support_resistance_max_zones_per_role: 4,
      support_resistance_max_total_zones: 8,
      support_resistance_max_significant_zones: 8,
      smart_money_max_liquidity_pools: 8,
    },
    councilControls: {
      min_dominance_margin: 0.18,
      flip_flop_release_stable_reads: 2,
      flip_flop_release_candidate_flips: 2,
      reversal_capture_min_dominance: 0.18,
      opportunity_capture_stable_reads: 3,
      opportunity_capture_min_score: 0.90,
      packet_valid_for_seconds: 60,
      study_packet_valid_for_seconds: 300,
    },
    overlayGenerationControls: {
      min_conf_global: 0.42,
      min_conf_latest: 0.50,
      history_depth: 8,
      label_density: 10,
      projection_focus: 0.35,
      debug_depth: 6,
      fuse_timeframe_overlays: false,
    },
    observerControls: {
      min_actionable_confidence: 0.58,
      min_thesis_confidence: 0.46,
      signal_cooldown_sec: 8,
      rl_track_interval_sec: 30,
    },
    runtimeControls: {
      consensus_threshold: 0.82,
      gates_pass_minimum: 9,
      conformal_max_interval_pct: 0.40,
      risk_min_pct: 0.5,
      risk_max_pct: 2.0,
      recall_boost_threshold: 0.85,
      recall_veto_threshold: 0.87,
      use_macro_local_alignment_gate: true,
      use_opposition_strength_gate: true,
      use_memory_ambiguity_penalty: true,
    },
  });

  const ROOT_SPECS = Object.freeze([
    {key: "modelConfidenceFloor", label: "Model floor", min: 0, max: 1, step: 0.01, mode: "percent"},
    {key: "executionThreshold", label: "Council gate", min: 0, max: 1, step: 0.01, mode: "percent"},
    {key: "overlayConfidenceFloor", label: "Overlay floor", min: 0, max: 1, step: 0.01, mode: "percent"},
  ]);

  const AI_SPECS = Object.freeze([
    {key: "market_intelligence", label: "Market intel"},
    {key: "decision_kernel", label: "Decision kernel"},
    {key: "smart_money", label: "Smart money"},
    {key: "memory_projection", label: "Memory"},
    {key: "lstm_sequence", label: "LSTM"},
    {key: "scenario_engine", label: "Scenarios"},
    {key: "high_frequency", label: "High freq"},
  ].map((spec) => ({...spec, min: 0, max: 2, step: 0.05, mode: "multiplier"})));

  const LANE_SPECS = Object.freeze([
    {key: "HIGH_FREQUENCY_TWO_CANDLE", label: "Two candle"},
    {key: "SNIPER_ZONE_ENTRY", label: "Sniper zone"},
    {key: "FAILED_RETEST_ENTRY", label: "Failed retest"},
    {key: "LOCAL_BREAKDOWN_CONTINUATION", label: "Breakdown"},
    {key: "HISTORY_MATCHED_CONTINUATION", label: "History"},
    {key: "MOMENTUM_ACCEPTANCE_ENTRY", label: "Momentum"},
  ].map((spec) => ({...spec, min: 0, max: 1, step: 0.01, mode: "percent"})));

  const CONTROL_GROUPS = Object.freeze([
    {
      key: "timingControls",
      title: "Timing + cadence",
      specs: [
        {key: "high_frequency_entry_grace_sec", label: "Entry grace", min: 0, max: 180, step: 1, mode: "seconds"},
        {key: "high_frequency_expiry_seconds", label: "Expiry", min: 60, max: 3600, step: 30, mode: "seconds"},
        {key: "high_frequency_horizon_candles", label: "HF candles", min: 1, max: 12, step: 1, mode: "count"},
        {key: "min_capture_interval_sec", label: "Min capture", min: 0.5, max: 10, step: 0.1, mode: "seconds"},
        {key: "max_capture_interval_sec", label: "Max capture", min: 0.5, max: 30, step: 0.1, mode: "seconds"},
        {key: "adaptive_timer_enabled", label: "Adaptive timer", type: "toggle"},
        {key: "phoenix_report_interval_sec", label: "Report sec", min: 0, max: 300, step: 1, mode: "seconds"},
      ],
    },
    {
      key: "memoryIdentityControls",
      title: "Memory + identity",
      specs: [
        {key: "auto_memory_projection", label: "Auto memory", type: "toggle"},
        {key: "require_memory_projection", label: "Memory gate", type: "toggle"},
        {key: "live_momentum_memory_advisory", label: "Memory advisory", type: "toggle"},
        {key: "require_market_identity", label: "Market gate", type: "toggle"},
        {key: "min_market_confidence", label: "Market conf", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "require_timeframe_identity", label: "TF gate", type: "toggle"},
        {key: "min_timeframe_confidence", label: "TF conf", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "broker_surface_cache_sec", label: "Surface cache", min: 2, max: 300, step: 1, mode: "seconds"},
        {key: "allow_locked_surface_identity_fallback", label: "Identity fallback", type: "toggle"},
      ],
    },
    {
      key: "scenarioControls",
      title: "Scenario + flow",
      specs: [
        {key: "scenario_generation_enabled", label: "Scenarios", type: "toggle"},
        {key: "continuous_model_feed_enabled", label: "Model feed", type: "toggle"},
        {key: "high_frequency_enabled", label: "High freq", type: "toggle"},
        {key: "two_candle_execution_allowed", label: "2C execute", type: "toggle"},
        {key: "swing_fallback_enabled", label: "Swing fallback", type: "toggle"},
        {key: "allow_location_sniper_entries", label: "Sniper entries", type: "toggle"},
      ],
    },
    {
      key: "riskControls",
      title: "Risk + guards",
      specs: [
        {key: "max_executions_per_window", label: "Max executes", min: 1, max: 20, step: 1, mode: "count"},
        {key: "execution_window_sec", label: "Window sec", min: 60, max: 3600, step: 30, mode: "seconds"},
        {key: "cooldown_sec", label: "Cooldown", min: 5, max: 3600, step: 5, mode: "seconds"},
        {key: "loss_guard_enabled", label: "Loss guard", type: "toggle"},
        {key: "loss_guard_max_consecutive_losses", label: "Max losses", min: 1, max: 10, step: 1, mode: "count"},
        {key: "loss_guard_window_sec", label: "Loss window", min: 60, max: 86400, step: 60, mode: "seconds"},
        {key: "loss_guard_pause_sec", label: "Loss pause", min: 60, max: 86400, step: 60, mode: "seconds"},
      ],
    },
    {
      key: "entryControls",
      title: "Entry logic",
      specs: [
        {key: "min_location_sniper_target_candles", label: "Sniper target", min: 1, max: 36, step: 1, mode: "count"},
        {key: "min_primary_target_candles", label: "Min target", min: 1, max: 72, step: 1, mode: "count"},
        {key: "max_primary_target_candles", label: "Max target", min: 1, max: 120, step: 1, mode: "count"},
        {key: "allow_live_momentum_entries", label: "Live momentum", type: "toggle"},
        {key: "min_live_momentum_visible_candles", label: "Visible candles", min: 1, max: 64, step: 1, mode: "count"},
        {key: "min_live_momentum_score", label: "Momentum score", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "min_live_momentum_alignment", label: "Alignment", min: 1, max: 10, step: 1, mode: "count"},
      ],
    },
    {
      key: "opposingForceControls",
      title: "Opposing force",
      specs: [
        {key: "allow_opposing_force_reactions", label: "Reactions", type: "toggle"},
        {key: "min_opposing_force_reaction_score", label: "Reaction score", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "min_opposing_force_reaction_alignment", label: "Alignment", min: 1, max: 10, step: 1, mode: "count"},
        {key: "min_opposing_force_reaction_risk", label: "Risk floor", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "min_opposing_force_reaction_entry_score", label: "Entry score", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "max_opposing_force_reaction_distance", label: "Max distance", min: 0, max: 1, step: 0.01, mode: "percent"},
      ],
    },
    {
      key: "structureControls",
      title: "S/R + smart money",
      specs: [
        {key: "live_max_tracked_candles", label: "Tracked bars", min: 8, max: 256, step: 1, mode: "count"},
        {key: "support_resistance_max_zones_per_role", label: "Zones/role", min: 2, max: 12, step: 1, mode: "count"},
        {key: "support_resistance_max_total_zones", label: "Total zones", min: 4, max: 24, step: 1, mode: "count"},
        {key: "support_resistance_max_significant_zones", label: "Sig zones", min: 4, max: 24, step: 1, mode: "count"},
        {key: "smart_money_max_liquidity_pools", label: "Liquidity pools", min: 4, max: 24, step: 1, mode: "count"},
      ],
    },
    {
      key: "councilControls",
      title: "Council micro gates",
      specs: [
        {key: "min_dominance_margin", label: "Dominance", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "flip_flop_release_stable_reads", label: "Stable reads", min: 1, max: 10, step: 1, mode: "count"},
        {key: "flip_flop_release_candidate_flips", label: "Max flips", min: 0, max: 10, step: 1, mode: "count"},
        {key: "reversal_capture_min_dominance", label: "Reversal dom", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "opportunity_capture_stable_reads", label: "Opp reads", min: 1, max: 10, step: 1, mode: "count"},
        {key: "opportunity_capture_min_score", label: "Opp score", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "packet_valid_for_seconds", label: "Packet TTL", min: 1, max: 300, step: 1, mode: "seconds"},
        {key: "study_packet_valid_for_seconds", label: "Study TTL", min: 5, max: 900, step: 5, mode: "seconds"},
      ],
    },
    {
      key: "overlayGenerationControls",
      title: "Overlay generation",
      specs: [
        {key: "min_conf_global", label: "Global conf", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "min_conf_latest", label: "Latest conf", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "history_depth", label: "History depth", min: 1, max: 24, step: 1, mode: "count"},
        {key: "label_density", label: "Label density", min: 1, max: 30, step: 1, mode: "count"},
        {key: "projection_focus", label: "Projection", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "debug_depth", label: "Debug depth", min: 0, max: 24, step: 1, mode: "count"},
        {key: "fuse_timeframe_overlays", label: "Fuse TF", type: "toggle"},
      ],
    },
    {
      key: "observerControls",
      title: "Observer + RL",
      specs: [
        {key: "min_actionable_confidence", label: "Actionable", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "min_thesis_confidence", label: "Thesis", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "signal_cooldown_sec", label: "Signal cool", min: 0, max: 300, step: 1, mode: "seconds"},
        {key: "rl_track_interval_sec", label: "RL track", min: 0.05, max: 300, step: 0.05, mode: "seconds"},
      ],
    },
    {
      key: "runtimeControls",
      title: "Runtime ensemble",
      specs: [
        {key: "consensus_threshold", label: "Consensus", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "gates_pass_minimum", label: "Gates min", min: 1, max: 20, step: 1, mode: "count"},
        {key: "conformal_max_interval_pct", label: "Interval max", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "risk_min_pct", label: "Risk min", min: 0, max: 10, step: 0.1, mode: "pctPoints"},
        {key: "risk_max_pct", label: "Risk max", min: 0, max: 10, step: 0.1, mode: "pctPoints"},
        {key: "recall_boost_threshold", label: "Recall boost", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "recall_veto_threshold", label: "Recall veto", min: 0, max: 1, step: 0.01, mode: "percent"},
        {key: "use_macro_local_alignment_gate", label: "Macro/local", type: "toggle"},
        {key: "use_opposition_strength_gate", label: "Opp strength", type: "toggle"},
        {key: "use_memory_ambiguity_penalty", label: "Ambiguity", type: "toggle"},
      ],
    },
  ]);

  const ALL_GROUP_KEYS = Object.freeze(CONTROL_GROUPS.map((group) => group.key));

  function clamp(value, fallback, min, max) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return fallback;
    }
    return Math.max(min, Math.min(max, number));
  }

  function boolValue(value, fallback) {
    if (typeof value === "boolean") {
      return value;
    }
    if (typeof value === "number") {
      return value !== 0;
    }
    if (typeof value === "string") {
      const normalized = value.trim().toLowerCase();
      if (["1", "true", "yes", "on"].includes(normalized)) return true;
      if (["0", "false", "no", "off"].includes(normalized)) return false;
    }
    return Boolean(fallback);
  }

  function normalizeMap(raw, defaults, min, max) {
    const source = raw && typeof raw === "object" ? raw : {};
    const out = {};
    Object.keys(defaults).forEach((key) => {
      out[key] = clamp(source[key], defaults[key], min, max);
    });
    return out;
  }

  function normalizeControlGroup(source, group) {
    const groupSource = source[group.key] && typeof source[group.key] === "object" ? source[group.key] : {};
    const defaults = DEFAULTS[group.key] || {};
    const out = {};
    group.specs.forEach((spec) => {
      const raw = groupSource[spec.key] ?? source[spec.key];
      if (spec.type === "toggle") {
        out[spec.key] = boolValue(raw, defaults[spec.key]);
      } else {
        out[spec.key] = clamp(raw, defaults[spec.key], spec.min, spec.max);
      }
    });
    return out;
  }

  function normalizeSettings(raw = {}) {
    const source = raw && typeof raw === "object" ? raw : {};
    const aiSource = source.aiStrengths && typeof source.aiStrengths === "object"
      ? source.aiStrengths
      : source.ai_contribution_strengths;
    const laneSource = source.laneThresholds && typeof source.laneThresholds === "object"
      ? source.laneThresholds
      : source.execution_lane_thresholds || source.lane_thresholds;
    const normalized = {
      schemaVersion: SCHEMA_VERSION,
      profileSaved: source.profileSaved === true,
      panelOpen: source.panelOpen !== false,
      panelLocked: source.panelLocked === true,
      modelConfidenceFloor: clamp(source.modelConfidenceFloor ?? source.model_confidence_floor, DEFAULTS.modelConfidenceFloor, 0, 1),
      executionThreshold: clamp(source.executionThreshold ?? source.execution_threshold, DEFAULTS.executionThreshold, 0, 1),
      overlayConfidenceFloor: clamp(source.overlayConfidenceFloor ?? source.overlay_min_confidence, DEFAULTS.overlayConfidenceFloor, 0, 1),
      aiStrengths: normalizeMap(aiSource, DEFAULTS.aiStrengths, 0, 2),
      laneThresholds: normalizeMap(laneSource, DEFAULTS.laneThresholds, 0, 1),
    };
    CONTROL_GROUPS.forEach((group) => {
      normalized[group.key] = normalizeControlGroup(source, group);
    });
    return normalized;
  }

  function flattenControlGroups(normalized) {
    const flat = {};
    ALL_GROUP_KEYS.forEach((groupKey) => {
      Object.assign(flat, normalized[groupKey] || {});
    });
    return flat;
  }

  function settingsToExecutionControls(settings) {
    const normalized = normalizeSettings(settings);
    const flatControls = flattenControlGroups(normalized);
    const profile = {
      schema_version: SCHEMA_VERSION,
      profile_saved: normalized.profileSaved === true,
      model_confidence_floor: normalized.modelConfidenceFloor,
      execution_threshold: normalized.executionThreshold,
      overlay_min_confidence: normalized.overlayConfidenceFloor,
      ai_contribution_strengths: {...normalized.aiStrengths},
      execution_lane_thresholds: {...normalized.laneThresholds},
      ...flatControls,
    };
    ALL_GROUP_KEYS.forEach((groupKey) => {
      profile[groupKey] = {...normalized[groupKey]};
    });
    return {
      model_confidence_floor: normalized.modelConfidenceFloor,
      high_frequency_min_confidence: normalized.modelConfidenceFloor,
      execution_threshold: normalized.executionThreshold,
      overlay_min_confidence: normalized.overlayConfidenceFloor,
      ai_contribution_strengths: {...normalized.aiStrengths},
      execution_lane_thresholds: {...normalized.laneThresholds},
      model_strength_profile: profile,
      ...flatControls,
    };
  }

  function outputValue(value, mode) {
    const number = Number(value || 0);
    if (mode === "multiplier") return `${number.toFixed(2)}x`;
    if (mode === "percent") return `${Math.round(number * 100)}%`;
    if (mode === "pctPoints") return `${number.toFixed(1)}%`;
    if (mode === "seconds") return `${number.toFixed(number < 10 && number % 1 ? 1 : 0)}s`;
    if (mode === "count") return String(Math.round(number));
    return number.toFixed(2);
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    }[char] || char));
  }

  function specFor(groupKey, key) {
    if (groupKey === "root") return ROOT_SPECS.find((spec) => spec.key === key);
    if (groupKey === "ai") return AI_SPECS.find((spec) => spec.key === key);
    if (groupKey === "lane") return LANE_SPECS.find((spec) => spec.key === key);
    const group = CONTROL_GROUPS.find((item) => item.key === groupKey);
    return group ? group.specs.find((spec) => spec.key === key) : null;
  }

  function valueFor(settings, groupKey, key) {
    if (groupKey === "root") return settings[key];
    if (groupKey === "ai") return settings.aiStrengths[key];
    if (groupKey === "lane") return settings.laneThresholds[key];
    return settings[groupKey] ? settings[groupKey][key] : undefined;
  }

  function setValue(settings, groupKey, key, value, checked) {
    const spec = specFor(groupKey, key);
    if (!spec) return;
    if (groupKey === "ai") {
      settings.aiStrengths[key] = clamp(value, settings.aiStrengths[key], spec.min, spec.max);
      return;
    }
    if (groupKey === "lane") {
      settings.laneThresholds[key] = clamp(value, settings.laneThresholds[key], spec.min, spec.max);
      return;
    }
    if (groupKey === "root") {
      settings[key] = clamp(value, settings[key], spec.min, spec.max);
      return;
    }
    if (!settings[groupKey]) settings[groupKey] = {};
    settings[groupKey][key] = spec.type === "toggle"
      ? checked === true
      : clamp(value, settings[groupKey][key], spec.min, spec.max);
  }

  function ensureStyle(documentRef) {
    if (documentRef.getElementById("model-strength-control-style")) {
      return;
    }
    const style = documentRef.createElement("style");
    style.id = "model-strength-control-style";
    style.textContent = `
      .model-strength-standalone .overlay-editor {
        display: grid;
        top: 8px;
        right: auto;
        bottom: auto;
        left: 8px;
        width: min(360px, calc(100vw - 16px));
        max-height: min(620px, calc(100svh - 16px));
        border-radius: 7px;
      }
      .model-strength-standalone .overlay-editor:not(.open) { display: none; }
      .model-strength-standalone .overlay-editor-fab { display: none; }
      .model-strength-panel .overlay-editor-head,
      .model-strength-panel .overlay-editor-foot { padding: 7px; gap: 6px; }
      .model-strength-panel .overlay-editor-title strong { font-size: 10px; }
      .model-strength-panel .overlay-editor-title span,
      .model-strength-panel .overlay-editor-status { font-size: 9px; }
      .model-strength-panel .overlay-editor-actions { gap: 4px; }
      .model-strength-panel .overlay-editor-actions button,
      .model-strength-panel .overlay-editor-foot button {
        min-height: 24px;
        padding: 5px 7px;
        border-radius: 4px;
        font-size: 9px;
      }
      .model-strength-panel .overlay-editor-body { padding: 7px; gap: 6px; overflow: auto; }
      .model-strength-panel .overlay-editor-section { padding: 0 0 6px; }
      .model-strength-panel .overlay-control-row {
        grid-template-columns: minmax(88px, .9fr) minmax(112px, 1fr) 48px;
        gap: 6px;
        min-height: 22px;
      }
      .model-strength-panel .overlay-check-row {
        display: grid;
        grid-template-columns: minmax(88px, 1fr) 22px 48px;
        align-items: center;
        gap: 6px;
        min-height: 22px;
        padding: 0;
      }
      .model-strength-panel .overlay-control-row label,
      .model-strength-panel .overlay-control-row output,
      .model-strength-panel .overlay-check-row label,
      .model-strength-panel .overlay-check-row output {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 9px;
      }
      .model-strength-panel .overlay-control-row input[type="range"] {
        min-width: 0;
        height: 18px;
      }
      .model-strength-panel .overlay-check-row input[type="checkbox"] {
        width: 14px;
        height: 14px;
        accent-color: #71cfff;
      }
      .model-strength-panel .model-strength-meter { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 5px; }
      .model-strength-panel .model-strength-meter span { min-width: 0; padding: 5px; border: 1px solid rgba(255,255,255,.06); border-radius: 4px; color: var(--muted); font: 8px var(--mono); text-transform: uppercase; background: rgba(255,255,255,.025); }
      .model-strength-panel .model-strength-meter strong { display: block; margin-top: 2px; color: var(--ice); font-size: 10px; }
      .model-strength-panel .model-strength-section {
        display: block;
        padding: 0;
        border: 1px solid rgba(255,255,255,.06);
        border-radius: 5px;
        overflow: hidden;
        background: rgba(255,255,255,.018);
      }
      .model-strength-panel .model-strength-section summary {
        min-height: 25px;
        padding: 6px 7px;
        cursor: pointer;
        color: var(--gold);
        font: 9px var(--mono);
        text-transform: uppercase;
        list-style-position: inside;
        background: rgba(255,255,255,.026);
      }
      .model-strength-panel .model-strength-section-body {
        display: grid;
        gap: 6px;
        padding: 7px;
      }
      .model-strength-panel.locked input { opacity: .54; }
      @media (max-width: 760px) {
        .model-strength-standalone .overlay-editor { width: min(334px, calc(100vw - 16px)); }
        .model-strength-panel .overlay-control-row { grid-template-columns: minmax(78px, .85fr) minmax(96px, 1fr) 42px; }
        .model-strength-panel .overlay-check-row { grid-template-columns: minmax(78px, 1fr) 20px 42px; }
        .model-strength-panel .model-strength-meter { grid-template-columns: 1fr; }
      }
    `;
    documentRef.head.appendChild(style);
  }

  function renderControl(spec, group, value) {
    const id = `model-strength-${group}-${spec.key}`.toLowerCase().replace(/[^a-z0-9_-]/g, "-");
    if (spec.type === "toggle") {
      return `
        <div class="overlay-check-row">
          <label for="${id}">${escapeHtml(spec.label)}</label>
          <input id="${id}" type="checkbox" ${value ? "checked" : ""} data-model-strength="${escapeHtml(spec.key)}" data-model-group="${group}" data-model-type="toggle">
          <output data-model-strength-output="${escapeHtml(spec.key)}" data-model-group="${group}">${value ? "On" : "Off"}</output>
        </div>
      `;
    }
    return `
      <div class="overlay-control-row">
        <label for="${id}">${escapeHtml(spec.label)}</label>
        <input id="${id}" type="range" min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${Number(value)}" data-model-strength="${escapeHtml(spec.key)}" data-model-group="${group}">
        <output data-model-strength-output="${escapeHtml(spec.key)}" data-model-group="${group}">${escapeHtml(outputValue(value, spec.mode))}</output>
      </div>
    `;
  }

  function buildSection(title, rows, open) {
    return `
      <details class="model-strength-section" aria-label="${escapeHtml(title)}"${open ? " open" : ""}>
        <summary>${escapeHtml(title)}</summary>
        <div class="model-strength-section-body">${rows}</div>
      </details>
    `;
  }

  function buildBody(settings) {
    const normalized = normalizeSettings(settings);
    const rootRows = ROOT_SPECS.map((spec) => renderControl(spec, "root", normalized[spec.key])).join("");
    const aiRows = AI_SPECS.map((spec) => renderControl(spec, "ai", normalized.aiStrengths[spec.key])).join("");
    const laneRows = LANE_SPECS.map((spec) => renderControl(spec, "lane", normalized.laneThresholds[spec.key])).join("");
    const groupedRows = CONTROL_GROUPS.map((group) => {
      const groupSettings = normalized[group.key] || {};
      const rows = group.specs.map((spec) => renderControl(spec, group.key, groupSettings[spec.key])).join("");
      return buildSection(group.title, rows, false);
    }).join("");
    return `
      <section class="overlay-editor-section" aria-label="Active profile">
        <div class="model-strength-meter">
          <span>Model floor<strong data-model-meter="model">${escapeHtml(outputValue(normalized.modelConfidenceFloor, "percent"))}</strong></span>
          <span>Council gate<strong data-model-meter="execution">${escapeHtml(outputValue(normalized.executionThreshold, "percent"))}</strong></span>
          <span>Overlay floor<strong data-model-meter="overlay">${escapeHtml(outputValue(normalized.overlayConfidenceFloor, "percent"))}</strong></span>
        </div>
      </section>
      ${buildSection("Core confidence", rootRows, true)}
      ${buildSection("AI contribution", aiRows, false)}
      ${buildSection("Execution lanes", laneRows, false)}
      ${groupedRows}
    `;
  }

  function readJsonResponse(response, fallback = {}) {
    if (!response.ok) {
      return Promise.reject(new Error(String(response.status)));
    }
    return response.text().then((text) => {
      if (!text) {
        return fallback;
      }
      try {
        return JSON.parse(text);
      } catch (_err) {
        return fallback;
      }
    });
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json", "Accept": "application/json"},
      cache: "no-store",
      body: JSON.stringify(body),
    }).then((response) => readJsonResponse(response, {}));
  }

  function publish(settings, controls, sessionId, source) {
    const payload = {type: "settings", sessionId, settings, controls, source, savedAt: Date.now()};
    try {
      if ("BroadcastChannel" in window) {
        const channel = new BroadcastChannel(CHANNEL_NAME);
        channel.postMessage(payload);
        channel.close();
      }
    } catch (_err) {
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (_err) {
    }
  }

  function readLocalSavedSettings() {
    try {
      const raw = localStorage.getItem(`${STORAGE_KEY}.saved`);
      if (!raw) return {};
      const payload = JSON.parse(raw);
      return payload && typeof payload === "object" ? payload : {};
    } catch (_err) {
      return {};
    }
  }

  function writeLocalSavedSettings(settings) {
    try {
      localStorage.setItem(`${STORAGE_KEY}.saved`, JSON.stringify(settings));
    } catch (_err) {
    }
  }

  function updateOne(panel, spec, group, value) {
    const input = panel.querySelector(`[data-model-strength="${spec.key}"][data-model-group="${group}"]`);
    const output = panel.querySelector(`[data-model-strength-output="${spec.key}"][data-model-group="${group}"]`);
    if (spec.type === "toggle") {
      if (input) input.checked = Boolean(value);
      if (output) output.textContent = value ? "On" : "Off";
      return;
    }
    if (input) input.value = String(value);
    if (output) output.textContent = outputValue(value, spec.mode);
  }

  function updatePanel(panel, settings) {
    const normalized = normalizeSettings(settings);
    ROOT_SPECS.forEach((spec) => updateOne(panel, spec, "root", normalized[spec.key]));
    AI_SPECS.forEach((spec) => updateOne(panel, spec, "ai", normalized.aiStrengths[spec.key]));
    LANE_SPECS.forEach((spec) => updateOne(panel, spec, "lane", normalized.laneThresholds[spec.key]));
    CONTROL_GROUPS.forEach((group) => {
      group.specs.forEach((spec) => updateOne(panel, spec, group.key, normalized[group.key][spec.key]));
    });
    const meters = {
      model: normalized.modelConfidenceFloor,
      execution: normalized.executionThreshold,
      overlay: normalized.overlayConfidenceFloor,
    };
    Object.keys(meters).forEach((key) => {
      const node = panel.querySelector(`[data-model-meter="${key}"]`);
      if (node) node.textContent = outputValue(meters[key], "percent");
    });
    panel.classList.toggle("locked", normalized.panelLocked === true);
    panel.querySelectorAll("[data-model-strength]").forEach((input) => {
      input.disabled = normalized.panelLocked === true;
    });
  }

  function create(options = {}) {
    const documentRef = options.document || document;
    const sessionId = String(options.sessionId || "pocket-live-8788");
    ensureStyle(documentRef);
    if (options.standalone) {
      documentRef.body.classList.add("model-strength-standalone");
    }
    const hardSaved = options.hardSavedSettings && Object.keys(options.hardSavedSettings).length
      ? options.hardSavedSettings
      : readLocalSavedSettings();
    let settings = normalizeSettings(hardSaved || {});
    let patchTimer = 0;

    const panel = documentRef.createElement("section");
    panel.className = "overlay-editor model-strength-panel open";
    panel.id = "model-strength-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Model strength controls");
    panel.innerHTML = `
      <div class="overlay-editor-head">
        <div class="overlay-editor-title">
          <strong>Model Strength</strong>
          <span id="model-strength-status">Advanced live tuning</span>
        </div>
        <div class="overlay-editor-actions">
          <button id="model-strength-lock" type="button">Lock</button>
          <button class="primary" id="model-strength-save" type="button">Save</button>
          <button id="model-strength-close" type="button">Hide</button>
        </div>
      </div>
      <div class="overlay-editor-body" id="model-strength-body">${buildBody(settings)}</div>
      <div class="overlay-editor-foot">
        <span class="overlay-editor-status" id="model-strength-saved">Profile live</span>
        <button id="model-strength-reset" type="button">Reset</button>
      </div>
    `;
    documentRef.body.appendChild(panel);

    const body = panel.querySelector("#model-strength-body");
    const lockButton = panel.querySelector("#model-strength-lock");
    const saveButton = panel.querySelector("#model-strength-save");
    const closeButton = panel.querySelector("#model-strength-close");
    const resetButton = panel.querySelector("#model-strength-reset");
    const statusText = panel.querySelector("#model-strength-status");
    const savedText = panel.querySelector("#model-strength-saved");

    function setSaved(saved, text) {
      if (savedText) {
        savedText.textContent = text || (saved ? "Profile saved" : "Unsaved live edits");
      }
    }

    function patchSoon(controls) {
      if (typeof options.patchControls !== "function") {
        return;
      }
      window.clearTimeout(patchTimer);
      patchTimer = window.setTimeout(() => {
        options.patchControls(controls).catch(() => {
          if (savedText) savedText.textContent = "Live patch failed";
        });
      }, 80);
    }

    function notify(source, patchLive) {
      const controls = settingsToExecutionControls(settings);
      publish(settings, controls, sessionId, source);
      if (typeof options.onSettingsChange === "function") {
        options.onSettingsChange(settings, controls);
      }
      if (patchLive !== false) {
        patchSoon(controls);
      }
    }

    function bindControls() {
      panel.querySelectorAll("[data-model-strength]").forEach((input) => {
        const eventName = input.getAttribute("data-model-type") === "toggle" ? "change" : "input";
        input.addEventListener(eventName, (event) => {
          const target = event.currentTarget;
          mutate(
            target.getAttribute("data-model-strength") || "",
            target.value,
            target.getAttribute("data-model-group") || "root",
            target.checked === true,
          );
        });
        if (eventName !== "change") {
          input.addEventListener("change", (event) => {
            const target = event.currentTarget;
            mutate(
              target.getAttribute("data-model-strength") || "",
              target.value,
              target.getAttribute("data-model-group") || "root",
              target.checked === true,
            );
          });
        }
      });
    }

    function rerender() {
      if (body) {
        body.innerHTML = buildBody(settings);
      }
      updatePanel(panel, settings);
      if (lockButton) {
        lockButton.textContent = settings.panelLocked ? "Unlock" : "Lock";
        lockButton.classList.toggle("locked", settings.panelLocked === true);
      }
      bindControls();
    }

    function mutate(key, value, group, checked) {
      if (settings.panelLocked) {
        return;
      }
      setValue(settings, group, key, value, checked);
      settings.profileSaved = false;
      updatePanel(panel, settings);
      setSaved(false);
      notify("model-strength-window", true);
    }

    if (lockButton) {
      lockButton.addEventListener("click", () => {
        settings.panelLocked = !settings.panelLocked;
        rerender();
        setSaved(false);
        notify("model-strength-lock", true);
      });
    }
    if (closeButton) {
      closeButton.addEventListener("click", () => {
        settings.panelOpen = false;
        panel.classList.remove("open");
        notify("model-strength-close", false);
      });
    }
    if (resetButton) {
      resetButton.addEventListener("click", () => {
        settings = normalizeSettings(DEFAULTS);
        rerender();
        setSaved(false, "Defaults restored");
        notify("model-strength-reset", true);
      });
    }
    if (saveButton) {
      saveButton.addEventListener("click", () => {
        const saveEndpoint = options.saveEndpoint || "/v1/mobile/window-tracker/floating-windows/model-strength/settings";
        const payload = {...settings, sessionId, session_id: sessionId, profileSaved: true};
        if (statusText) statusText.textContent = "Saving profile";
        postJson(saveEndpoint, payload)
          .then((response) => {
            settings = normalizeSettings(response && response.settings ? response.settings : payload);
            settings.profileSaved = true;
            writeLocalSavedSettings(settings);
            rerender();
            setSaved(true, response && response.applied === false ? "Saved; session not found" : "Saved to live profile");
            if (statusText) statusText.textContent = "Saved profile active";
            notify("model-strength-save", true);
          })
          .catch(() => {
            settings.profileSaved = false;
            writeLocalSavedSettings(settings);
            const controls = settingsToExecutionControls(settings);
            const finishFallback = (patched) => {
              rerender();
              setSaved(false, patched ? "Live patched; backend save failed" : "Local draft; backend save failed");
              if (statusText) statusText.textContent = patched ? "Live patch active; profile unsaved" : "Profile save failed";
              notify("model-strength-save-fallback", false);
            };
            if (typeof options.patchControls === "function") {
              options.patchControls(controls).then(() => finishFallback(true)).catch(() => finishFallback(false));
            } else {
              finishFallback(false);
            }
          });
      });
    }

    rerender();
    notify("model-strength-create", false);
    return {
      panel,
      getSettings: () => normalizeSettings(settings),
      applySettings: (nextSettings, opts = {}) => {
        settings = normalizeSettings(nextSettings);
        rerender();
        notify("model-strength-apply", opts.patchLive !== false);
      },
    };
  }

  function inlineWindowHtml(sessionId, saveEndpoint) {
    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PhoenixGuard Model Strength</title>
  <style>
    :root { --ice: #8fe7ff; --gold: #f2c866; --muted: #8ea4b8; --text: #eef6ff; --mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace; }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; margin: 0; background: #05090d; color: var(--text); font-family: Inter, Segoe UI, Arial, sans-serif; overflow: hidden; }
  </style>
  <link rel="stylesheet" href="/v1/mobile/window-tracker/assets/floating-windows/overlay_editor.css">
  <script src="/v1/mobile/window-tracker/assets/floating-windows/model_strength_controls.js?v=model-strength-advanced-v1"><\/script>
</head>
<body class="model-strength-standalone">
  <script>
    const SESSION_ID = ${JSON.stringify(sessionId)};
    const SAVE_ENDPOINT = ${JSON.stringify(saveEndpoint)};
    function patchControls(body) {
      return fetch(\`/v1/mobile/window-tracker/sessions/\${encodeURIComponent(SESSION_ID)}/controls\`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json", "Accept": "application/json"},
        cache: "no-store",
        body: JSON.stringify(body),
      }).then((response) => response.ok ? response.json() : Promise.reject(new Error(String(response.status))));
    }
    function showBootError() {
      document.body.innerHTML = '<div style="margin:12px;padding:12px;border:1px solid rgba(255,255,255,.14);border-radius:6px;color:#eef6ff;background:#11100d;font:12px Consolas,monospace;">Model strength controls failed to load.</div>';
    }
    function boot(settings) {
      const factory = window.PhoenixGuardModelStrengthControls;
      if (!factory || typeof factory.create !== "function") {
        showBootError();
        return;
      }
      factory.create({
        document,
        standalone: true,
        sessionId: SESSION_ID,
        hardSavedSettings: settings,
        saveEndpoint: SAVE_ENDPOINT,
        patchControls,
      });
    }
    fetch(SAVE_ENDPOINT, {cache: "no-store", headers: {"Accept": "application/json"}})
      .then((response) => response.ok ? response.text().then((text) => text ? JSON.parse(text) : {}) : {})
      .catch(() => ({}))
      .then(boot);
  <\/script>
</body>
</html>`;
  }

  function openStandaloneWindow(sessionId, saveEndpoint) {
    const resolvedSessionId = String(sessionId || "pocket-live-8788");
    const resolvedEndpoint = saveEndpoint || "/v1/mobile/window-tracker/floating-windows/model-strength/settings";
    const popup = window.open("", "phoenixguard_model_strength", "width=382,height=640,resizable=yes,scrollbars=no");
    if (popup) {
      popup.document.open();
      popup.document.write(inlineWindowHtml(resolvedSessionId, resolvedEndpoint));
      popup.document.close();
      popup.focus();
      return popup;
    }
    const url = `/v1/mobile/window-tracker/floating-windows/model-strength?session_id=${encodeURIComponent(resolvedSessionId)}`;
    return window.open(url, "phoenixguard_model_strength", "width=382,height=640,resizable=yes,scrollbars=no,noopener");
  }

  function createDashboardBridge(options = {}) {
    const documentRef = options.document || document;
    const sessionId = String(options.sessionId || "pocket-live-8788");
    const saveEndpoint = options.saveEndpoint || "/v1/mobile/window-tracker/floating-windows/model-strength/settings";
    const hardSaved = options.hardSavedSettings && Object.keys(options.hardSavedSettings).length
      ? options.hardSavedSettings
      : readLocalSavedSettings();
    let settings = normalizeSettings(hardSaved || {});

    function apply(nextSettings, source) {
      settings = normalizeSettings(nextSettings);
      const controls = settingsToExecutionControls(settings);
      if (typeof options.onSettingsChange === "function") {
        options.onSettingsChange(settings, controls, source);
      }
    }

    const button = documentRef.getElementById("model-strength-open");
    if (button) {
      button.hidden = false;
      button.addEventListener("click", () => openStandaloneWindow(sessionId, saveEndpoint));
    }

    if ("BroadcastChannel" in window) {
      const channel = new BroadcastChannel(CHANNEL_NAME);
      channel.addEventListener("message", (event) => {
        const payload = event.data || {};
        if (payload.type === "settings" && (!payload.sessionId || payload.sessionId === sessionId)) {
          apply(payload.settings, payload.source || "broadcast");
        }
      });
    }
    window.addEventListener("storage", (event) => {
      if (event.key !== STORAGE_KEY || !event.newValue) {
        return;
      }
      try {
        const payload = JSON.parse(event.newValue);
        if (payload && payload.type === "settings" && (!payload.sessionId || payload.sessionId === sessionId)) {
          apply(payload.settings, payload.source || "storage");
        }
      } catch (_err) {
      }
    });

    fetch(saveEndpoint, {cache: "no-store", headers: {"Accept": "application/json"}})
      .then((response) => readJsonResponse(response, settings))
      .then((saved) => apply(saved, "saved-profile"))
      .catch(() => apply(settings, "hard-saved"));

    apply(settings, "initial");
    return {
      getSettings: () => normalizeSettings(settings),
      applySettings: apply,
      open: () => openStandaloneWindow(sessionId, saveEndpoint),
    };
  }

  window.PhoenixGuardModelStrengthControls = {
    create,
    createDashboardBridge,
    normalizeSettings,
    settingsToExecutionControls,
    openStandaloneWindow,
  };
}());
