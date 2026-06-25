/* PhoenixGuard Keyboard Shortcuts System */

(function() {
  'use strict';

  class KeyboardShortcuts {
    constructor() {
      this.shortcuts = new Map();
      this.helpVisible = false;
      this.setupShortcuts();
      this.attachListeners();
    }

    // =========================================================================
    // SETUP SHORTCUTS
    // =========================================================================

    setupShortcuts() {
      // Ctrl+K (Windows) / Cmd+K (Mac) - Open command palette
      this.register('CommandPalette', {
        key: 'k',
        ctrl: true,
        title: 'Open command palette',
        action: () => this.openCommandPalette(),
      });

      // Ctrl+Enter / Cmd+Enter - Execute analysis
      this.register('RunAnalysis', {
        key: 'Enter',
        ctrl: true,
        title: 'Run analysis',
        action: () => this.runAnalysis(),
      });

      // Escape - Close dialogs / clear selections
      this.register('Escape', {
        key: 'Escape',
        title: 'Close dialogs and clear selections',
        action: () => this.handleEscape(),
      });

      // Ctrl+1 / Cmd+1 - Jump to Analysis tab
      this.register('TabAnalysis', {
        key: '1',
        ctrl: true,
        title: 'Go to Analysis tab',
        action: () => this.switchTab('Analysis'),
      });

      // Ctrl+2 / Cmd+2 - Jump to Visual Lab tab
      this.register('TabVisualLab', {
        key: '2',
        ctrl: true,
        title: 'Go to Visual Lab tab',
        action: () => this.switchTab('Visual Lab'),
      });

      // Ctrl+3 / Cmd+3 - Jump to Monitoring tab
      this.register('TabMonitoring', {
        key: '3',
        ctrl: true,
        title: 'Go to Monitoring tab',
        action: () => this.switchTab('Monitoring'),
      });

      // Ctrl+4 / Cmd+4 - Jump to Feed tab
      this.register('TabFeed', {
        key: '4',
        ctrl: true,
        title: 'Go to Feed tab',
        action: () => this.switchTab('Feed'),
      });

      // Ctrl+S / Cmd+S - Save current state as preset
      this.register('SavePreset', {
        key: 's',
        ctrl: true,
        title: 'Save current parameters as preset',
        action: (e) => {
          e.preventDefault();
          this.saveAsPreset();
        },
      });

      // Ctrl+? / Cmd+? - Toggle help overlay
      this.register('Help', {
        key: '?',
        ctrl: true,
        title: 'Show keyboard shortcuts help',
        action: () => this.toggleHelp(),
      });
    }

    register(id, config) {
      this.shortcuts.set(id, config);
    }

    // =========================================================================
    // EVENT HANDLING
    // =========================================================================

    attachListeners() {
      document.addEventListener('keydown', (e) => this.handleKeyDown(e));
    }

    handleKeyDown(e) {
      // Don't trigger shortcuts in input fields (except for special cases)
      const isInput = e.target.matches('input[type="text"], input[type="number"], textarea');
      if (isInput && !['Enter', 'Escape'].includes(e.key)) {
        return;
      }

      // Check for shortcut matches
      for (const [id, config] of this.shortcuts) {
        if (this.matchesShortcut(e, config)) {
          e.preventDefault();
          config.action(e);
          this.showToast(`Shortcut: ${config.title}`);
          return;
        }
      }
    }

    matchesShortcut(e, config) {
      const keyMatch = e.key.toLowerCase() === config.key.toLowerCase();
      const ctrlMatch = config.ctrl ? e.ctrlKey || e.metaKey : !e.ctrlKey && !e.metaKey;
      const shiftMatch = config.shift ? e.shiftKey : !e.shiftKey;
      const altMatch = config.alt ? e.altKey : !e.altKey;

      return keyMatch && ctrlMatch && shiftMatch && altMatch;
    }

    // =========================================================================
    // SHORTCUT ACTIONS
    // =========================================================================

    openCommandPalette() {
      // Dispatch custom event for command palette component
      window.dispatchEvent(new CustomEvent('pg:openCommandPalette'));
    }

    runAnalysis() {
      // Find and click the run button
      const runBtn = document.querySelector('[data-testid="run_btn"]') ||
                     document.querySelector('button:has-text("Run")') ||
                     Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Run'));

      if (runBtn) {
        runBtn.click();
        this.showToast('Analysis started', 'info');
      }
    }

    handleEscape() {
      // Close command palette if open
      window.dispatchEvent(new CustomEvent('pg:closeCommandPalette'));

      // Close help if open
      if (this.helpVisible) {
        this.toggleHelp();
      }

      // Close any modals
      const modals = document.querySelectorAll('.pg-modal');
      modals.forEach(modal => {
        if (modal.style.display !== 'none') {
          modal.style.display = 'none';
        }
      });

      // Clear text inputs
      const inputs = document.querySelectorAll('input[type="text"]:focus, textarea:focus');
      inputs.forEach(input => input.blur());
    }

    switchTab(tabName) {
      // Find tab button with matching text
      const tabButtons = Array.from(document.querySelectorAll('.gradio-tabs button'));
      const targetTab = tabButtons.find(btn => btn.textContent.includes(tabName));

      if (targetTab) {
        targetTab.click();
        this.showToast(`Switched to ${tabName}`, 'info');
      }
    }

    saveAsPreset() {
      // Get current parameter values
      const params = this.captureCurrentParams();

      // Show preset name dialog
      const presetName = prompt('Enter preset name (e.g., "Conservative Strategy"):');
      if (!presetName || presetName.trim() === '') {
        return;
      }

      // Save to persistent state
      if (window.PhoenixGuardState) {
        const success = window.PhoenixGuardState.savePreset(presetName.trim(), params);
        if (success) {
          this.showToast(`Preset "${presetName}" saved`, 'success');
        } else {
          this.showToast('Could not save preset (maximum reached)', 'warning');
        }
      }
    }

    toggleHelp() {
      this.helpVisible = !this.helpVisible;
      window.dispatchEvent(new CustomEvent('pg:toggleHelp', {
        detail: { visible: this.helpVisible }
      }));
    }

    // =========================================================================
    // UTILITY FUNCTIONS
    // =========================================================================

    captureCurrentParams() {
      const params = {};

      // Capture all input values
      document.querySelectorAll('[data-testid*="slider"], input[type="range"]').forEach(el => {
        params[el.id || el.name || 'slider_' + Math.random()] = parseFloat(el.value);
      });

      // Capture all select values
      document.querySelectorAll('select').forEach(el => {
        params[el.id || el.name || 'select_' + Math.random()] = el.value;
      });

      // Capture all checkbox values
      document.querySelectorAll('input[type="checkbox"]').forEach(el => {
        params[el.id || el.name || 'checkbox_' + Math.random()] = el.checked;
      });

      return params;
    }

    showToast(message, type = 'info') {
      const toast = document.createElement('div');
      toast.className = `pg-toast ${type}`;
      toast.textContent = message;
      toast.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        z-index: 10000;
      `;

      document.body.appendChild(toast);

      // Auto-remove after 3 seconds
      setTimeout(() => {
        toast.style.animation = 'fadeOut 0.3s ease-out forwards';
        setTimeout(() => toast.remove(), 300);
      }, 3000);
    }

    // =========================================================================
    // PUBLIC API
    // =========================================================================

    getShortcuts() {
      const shortcuts = [];
      for (const [id, config] of this.shortcuts) {
        shortcuts.push({
          id,
          ...config,
          keys: this.formatShortcut(config),
        });
      }
      return shortcuts;
    }

    formatShortcut(config) {
      const parts = [];
      if (config.ctrl) {
        parts.push(navigator.platform.includes('Mac') ? 'Cmd' : 'Ctrl');
      }
      if (config.shift) parts.push('Shift');
      if (config.alt) parts.push('Alt');
      parts.push(config.key);
      return parts.join('+');
    }

    debugInfo() {
      return {
        shortcutsCount: this.shortcuts.size,
        shortcuts: this.getShortcuts(),
      };
    }
  }

  // =========================================================================
  // GLOBAL INSTANCE
  // =========================================================================

  window.PhoenixGuardShortcuts = new KeyboardShortcuts();

  console.log('[PhoenixGuard] Keyboard Shortcuts initialized');
  console.log('[PhoenixGuard] Press Ctrl+? for help');
})();
