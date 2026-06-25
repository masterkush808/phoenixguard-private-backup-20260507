/* PhoenixGuard Persistent State Manager */

(function() {
  'use strict';

  const STORAGE_KEY = 'phoenixguard_preferences';
  const STORAGE_VERSION = 1;
  const MAX_PRESETS = 10;
  const CLEANUP_DAYS = 30;

  class PersistentStateManager {
    constructor() {
      this.data = this.loadData();
      this.setupAutoSave();
    }

    // =========================================================================
    // CORE STORAGE OPERATIONS
    // =========================================================================

    loadData() {
      try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (!stored) {
          return this.getDefaultData();
        }

        const parsed = JSON.parse(stored);
        if (parsed.version !== STORAGE_VERSION) {
          console.warn('[PhoenixGuard] Storage version mismatch, using defaults');
          return this.getDefaultData();
        }

        this.cleanupOldData(parsed);
        return parsed;
      } catch (error) {
        console.error('[PhoenixGuard] Failed to load preferences:', error);
        return this.getDefaultData();
      }
    }

    saveData() {
      try {
        const toSave = {
          version: STORAGE_VERSION,
          timestamp: new Date().toISOString(),
          ...this.data,
        };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
      } catch (error) {
        if (error.name === 'QuotaExceededError') {
          console.warn('[PhoenixGuard] localStorage quota exceeded');
          this.clearOldPresets();
        } else {
          console.error('[PhoenixGuard] Failed to save preferences:', error);
        }
      }
    }

    getDefaultData() {
      return {
        theme: 'dark',
        accentColor: 'blue',
        panelState: {},
        lastRunParams: {},
        presets: {},
        createdAt: new Date().toISOString(),
      };
    }

    // =========================================================================
    // THEME & ACCENT MANAGEMENT
    // =========================================================================

    setTheme(theme) {
      if (!['light', 'dark'].includes(theme)) return;
      this.data.theme = theme;
      document.documentElement.setAttribute('data-theme', theme);
      this.saveData();
      this.dispatchEvent('themeChange', { theme });
    }

    getTheme() {
      return this.data.theme;
    }

    setAccentColor(accent) {
      const validAccents = ['blue', 'purple', 'emerald', 'amber', 'rose', 'cyan'];
      if (!validAccents.includes(accent)) return;
      this.data.accentColor = accent;
      document.documentElement.setAttribute('data-accent', accent);
      this.saveData();
      this.dispatchEvent('accentChange', { accent });
    }

    getAccentColor() {
      return this.data.accentColor;
    }

    // =========================================================================
    // PANEL STATE MANAGEMENT
    // =========================================================================

    savePanelState(panelId, state) {
      if (!this.data.panelState) {
        this.data.panelState = {};
      }
      this.data.panelState[panelId] = {
        ...state,
        savedAt: new Date().toISOString(),
      };
      this.saveData();
    }

    getPanelState(panelId) {
      return this.data.panelState?.[panelId] || null;
    }

    allPanelStates() {
      return this.data.panelState || {};
    }

    // =========================================================================
    // RUN PARAMETERS MANAGEMENT
    // =========================================================================

    saveRunParams(params) {
      this.data.lastRunParams = {
        ...params,
        savedAt: new Date().toISOString(),
      };
      this.saveData();
      this.dispatchEvent('paramsChange', { params });
    }

    getRunParams() {
      const params = { ...this.data.lastRunParams };
      delete params.savedAt;
      return params;
    }

    mergeRunParams(newParams) {
      const current = this.getRunParams();
      const merged = { ...current, ...newParams };
      this.saveRunParams(merged);
      return merged;
    }

    // =========================================================================
    // PRESET MANAGEMENT
    // =========================================================================

    savePreset(name, params) {
      if (!this.data.presets) {
        this.data.presets = {};
      }

      // Limit to MAX_PRESETS
      const presetCount = Object.keys(this.data.presets).length;
      if (presetCount >= MAX_PRESETS && !this.data.presets[name]) {
        console.warn(`[PhoenixGuard] Maximum presets (${MAX_PRESETS}) reached`);
        return false;
      }

      this.data.presets[name] = {
        params: { ...params },
        createdAt: new Date().toISOString(),
        usageCount: this.data.presets[name]?.usageCount || 0,
      };

      this.saveData();
      this.dispatchEvent('presetSaved', { name });
      return true;
    }

    loadPreset(name) {
      const preset = this.data.presets?.[name];
      if (!preset) {
        console.warn(`[PhoenixGuard] Preset not found: ${name}`);
        return null;
      }

      // Track usage
      preset.usageCount = (preset.usageCount || 0) + 1;
      preset.lastUsedAt = new Date().toISOString();
      this.saveData();

      this.dispatchEvent('presetLoaded', { name, params: preset.params });
      return preset.params;
    }

    listPresets() {
      if (!this.data.presets) return [];
      return Object.keys(this.data.presets).map(name => ({
        name,
        ...this.data.presets[name],
      }));
    }

    deletePreset(name) {
      if (this.data.presets && this.data.presets[name]) {
        delete this.data.presets[name];
        this.saveData();
        this.dispatchEvent('presetDeleted', { name });
        return true;
      }
      return false;
    }

    renamePreset(oldName, newName) {
      if (!this.data.presets || !this.data.presets[oldName]) {
        return false;
      }

      this.data.presets[newName] = this.data.presets[oldName];
      delete this.data.presets[oldName];
      this.saveData();
      this.dispatchEvent('presetRenamed', { oldName, newName });
      return true;
    }

    // =========================================================================
    // CLEANUP UTILITIES
    // =========================================================================

    cleanupOldData(data) {
      if (!data.timestamp) return;

      const storedDate = new Date(data.timestamp);
      const now = new Date();
      const daysDiff = (now - storedDate) / (1000 * 60 * 60 * 24);

      if (daysDiff > CLEANUP_DAYS) {
        console.log('[PhoenixGuard] Data older than', CLEANUP_DAYS, 'days, clearing');
        localStorage.removeItem(STORAGE_KEY);
      }
    }

    clearOldPresets() {
      if (!this.data.presets) return;

      const presets = this.listPresets();
      presets.sort((a, b) => b.usageCount - a.usageCount);

      // Keep top 5 most used presets when quota exceeded
      const toKeep = presets.slice(0, 5).map(p => p.name);
      const keys = Object.keys(this.data.presets);

      keys.forEach(key => {
        if (!toKeep.includes(key)) {
          delete this.data.presets[key];
        }
      });

      this.saveData();
    }

    clearAll() {
      if (confirm('Are you sure you want to clear all saved preferences?')) {
        localStorage.removeItem(STORAGE_KEY);
        this.data = this.getDefaultData();
        this.dispatchEvent('dataCleared', {});
      }
    }

    // =========================================================================
    // AUTO-SAVE FUNCTIONALITY
    // =========================================================================

    setupAutoSave() {
      // Save on page unload
      window.addEventListener('beforeunload', () => this.saveData());

      // Periodic save every 30 seconds if data changed
      this.autoSaveInterval = setInterval(() => {
        this.saveData();
      }, 30000);
    }

    teardown() {
      if (this.autoSaveInterval) {
        clearInterval(this.autoSaveInterval);
      }
    }

    // =========================================================================
    // EVENT SYSTEM
    // =========================================================================

    dispatchEvent(eventName, detail) {
      const event = new CustomEvent(`pg:${eventName}`, { detail });
      window.dispatchEvent(event);
    }

    on(eventName, callback) {
      window.addEventListener(`pg:${eventName}`, (e) => callback(e.detail));
    }

    off(eventName, callback) {
      window.removeEventListener(`pg:${eventName}`, callback);
    }

    // =========================================================================
    // DEBUGGING
    // =========================================================================

    debugInfo() {
      return {
        data: this.data,
        storageKey: STORAGE_KEY,
        storageSize: new Blob([JSON.stringify(this.data)]).size,
        presetCount: Object.keys(this.data.presets || {}).length,
      };
    }

    exportData() {
      return JSON.stringify(this.data, null, 2);
    }

    importData(jsonString) {
      try {
        const imported = JSON.parse(jsonString);
        if (!imported.version) {
          throw new Error('Invalid data format');
        }
        this.data = imported;
        this.saveData();
        this.dispatchEvent('dataImported', {});
        return true;
      } catch (error) {
        console.error('[PhoenixGuard] Failed to import data:', error);
        return false;
      }
    }
  }

  // =========================================================================
  // GLOBAL INSTANCE
  // =========================================================================

  window.PhoenixGuardState = new PersistentStateManager();

  // Apply saved theme and accent on load
  document.documentElement.setAttribute('data-theme', window.PhoenixGuardState.getTheme());
  document.documentElement.setAttribute('data-accent', window.PhoenixGuardState.getAccentColor());

  console.log('[PhoenixGuard] State Manager initialized');
})();
