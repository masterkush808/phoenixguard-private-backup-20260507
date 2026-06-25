/* PhoenixGuard Enhancement System Bootstrap */

(function() {
  'use strict';

  window.PhoenixGuardEnhancements = {
    version: '1.0.0',
    initialized: false,

    init() {
      if (this.initialized) return;

      // Wait for DOM to be ready
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => this.bootstrap());
      } else {
        this.bootstrap();
      }
    },

    bootstrap() {
      console.log('[PhoenixGuard Enhancements] Bootstrap starting...');

      // Verify all components are loaded
      this.verifyComponents();

      // Initialize in order
      this.initThemeSystem();
      this.initStateManager();
      this.initKeyboardShortcuts();
      this.initMetricAnimations();

      // Show welcome message
      this.showWelcome();

      this.initialized = true;
      console.log('[PhoenixGuard Enhancements] ✅ All systems initialized');

      // Emit initialization event
      window.dispatchEvent(new CustomEvent('pg:systemReady'));
    },

    verifyComponents() {
      const required = [
        'PhoenixGuardState',
        'PhoenixGuardShortcuts',
        'PhoenixGuardCommandPalette',
        'PhoenixGuardThemeSwitcher',
        'PhoenixGuardMetricAnimator',
        'PhoenixGuardHelpOverlay',
      ];

      const missing = required.filter(comp => !window[comp]);
      if (missing.length > 0) {
        console.warn('[PhoenixGuard] Missing components:', missing);
      } else {
        console.log('[PhoenixGuard] ✅ All components loaded');
      }
    },

    initThemeSystem() {
      const current = window.PhoenixGuardState?.getTheme() || 'dark';
      const accent = window.PhoenixGuardState?.getAccentColor() || 'blue';
      document.documentElement.setAttribute('data-theme', current);
      document.documentElement.setAttribute('data-accent', accent);
      console.log(`[PhoenixGuard] Theme: ${current}, Accent: ${accent}`);
    },

    initStateManager() {
      // Restore last run parameters
      const lastParams = window.PhoenixGuardState?.getRunParams();
      if (lastParams && Object.keys(lastParams).length > 0) {
        console.log('[PhoenixGuard] Restoring last session parameters');
        this.restoreParameters(lastParams);
      }

      // Listen for state changes
      window.PhoenixGuardState?.on('themeChange', (detail) => {
        console.log('[PhoenixGuard] Theme changed to:', detail.theme);
      });

      window.PhoenixGuardState?.on('paramsChange', (detail) => {
        console.log('[PhoenixGuard] Parameters saved');
      });
    },

    initKeyboardShortcuts() {
      console.log('[PhoenixGuard] Keyboard shortcuts ready. Press Ctrl+? for help');
    },

    initMetricAnimations() {
      console.log('[PhoenixGuard] Real-time metric animations active');
    },

    restoreParameters(params) {
      // Find and restore slider values
      document.querySelectorAll('input[type="range"]').forEach(slider => {
        const id = slider.id || slider.name;
        if (params[id] !== undefined) {
          slider.value = params[id];
          slider.dispatchEvent(new Event('input', { bubbles: true }));
        }
      });

      // Find and restore select values
      document.querySelectorAll('select').forEach(select => {
        const id = select.id || select.name;
        if (params[id] !== undefined) {
          select.value = params[id];
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });

      // Find and restore checkbox values
      document.querySelectorAll('input[type="checkbox"]').forEach(checkbox => {
        const id = checkbox.id || checkbox.name;
        if (params[id] !== undefined) {
          checkbox.checked = params[id];
          checkbox.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });
    },

    showWelcome() {
      const hasVisited = localStorage.getItem('pg_welcomed');
      if (!hasVisited) {
        setTimeout(() => {
          window.PhoenixGuardShortcuts?.showToast(
            '🎉 Welcome! Press Ctrl+? to see keyboard shortcuts',
            'info'
          );
          localStorage.setItem('pg_welcomed', 'true');
        }, 1000);
      }
    },

    debug() {
      return {
        version: this.version,
        initialized: this.initialized,
        state: window.PhoenixGuardState?.debugInfo?.(),
        shortcuts: window.PhoenixGuardShortcuts?.debugInfo?.(),
        commandPalette: window.PhoenixGuardCommandPalette && 'loaded',
        themeSwitcher: window.PhoenixGuardThemeSwitcher && 'loaded',
        metricAnimator: window.PhoenixGuardMetricAnimator && 'loaded',
        helpOverlay: window.PhoenixGuardHelpOverlay && 'loaded',
      };
    },
  };

  // Auto-initialize on load
  window.PhoenixGuardEnhancements.init();

  // Export for debugging
  window.pgDebug = () => {
    console.table(window.PhoenixGuardEnhancements.debug());
  };

  console.log('[PhoenixGuard] Type pgDebug() in console for system info');
})();
