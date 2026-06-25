/* PhoenixGuard Command Palette */

(function() {
  'use strict';

  class CommandPalette {
    constructor() {
      this.isOpen = false;
      this.selectedIndex = 0;
      this.searchQuery = '';
      this.setupDOM();
      this.attachListeners();
    }

    setupDOM() {
      const html = `
        <div id="pg-command-palette-overlay" class="pg-modal-overlay" style="display:none;">
          <div class="pg-command-palette pg-modal">
            <div class="pg-command-search">
              <input id="pg-command-input" type="text" placeholder="Type a command... (ESC to close)" autocomplete="off">
            </div>
            <div id="pg-command-list" class="pg-command-list"></div>
            <div class="pg-command-hint">↑ ↓ Enter to select • ESC to close</div>
          </div>
        </div>
      `;
      document.body.insertAdjacentHTML('beforeend', html);
      this.overlay = document.getElementById('pg-command-palette-overlay');
      this.input = document.getElementById('pg-command-input');
      this.list = document.getElementById('pg-command-list');
    }

    attachListeners() {
      window.addEventListener('pg:openCommandPalette', () => this.open());
      window.addEventListener('pg:closeCommandPalette', () => this.close());
      this.input.addEventListener('input', (e) => this.search(e.target.value));
      this.input.addEventListener('keydown', (e) => this.handleKeyDown(e));
    }

    getCommands() {
      const commands = [
        { id: 'run', title: 'Run Analysis', description: 'Execute current analysis', icon: '▶️' },
        { id: 'save-preset', title: 'Save as Preset', description: 'Save current parameters', icon: '💾' },
        { id: 'load-preset', title: 'Load Preset', description: 'Load a saved preset', icon: '📂' },
        { id: 'theme-light', title: 'Switch to Light Mode', description: 'Enable light theme', icon: '☀️' },
        { id: 'theme-dark', title: 'Switch to Dark Mode', description: 'Enable dark theme', icon: '🌙' },
        { id: 'help', title: 'Show Help', description: 'Display keyboard shortcuts', icon: '❓' },
        { id: 'export-state', title: 'Export State', description: 'Export all preferences', icon: '⬇️' },
        { id: 'clear-all', title: 'Clear All Data', description: 'Reset all preferences', icon: '🗑️' },
      ];

      if (window.PhoenixGuardState) {
        window.PhoenixGuardState.listPresets().forEach(preset => {
          commands.push({
            id: `preset-${preset.name}`,
            title: `Load: ${preset.name}`,
            description: `Used ${preset.usageCount} times`,
            icon: '📌',
          });
        });
      }

      return commands;
    }

    search(query) {
      this.searchQuery = query;
      const commands = this.getCommands();
      const filtered = commands.filter(cmd =>
        cmd.title.toLowerCase().includes(query.toLowerCase()) ||
        cmd.description.toLowerCase().includes(query.toLowerCase())
      );

      this.selectedIndex = 0;
      this.renderList(filtered);
    }

    renderList(commands) {
      this.list.innerHTML = '';
      commands.forEach((cmd, index) => {
        const div = document.createElement('div');
        div.className = 'pg-command-item' + (index === this.selectedIndex ? ' selected' : '');
        div.innerHTML = `<span class="pg-command-icon">${cmd.icon}</span><div><div class="pg-command-title">${cmd.title}</div><div class="pg-command-desc">${cmd.description}</div></div>`;
        div.addEventListener('click', () => this.execute(cmd.id));
        this.list.appendChild(div);
      });
    }

    handleKeyDown(e) {
      const items = this.list.querySelectorAll('.pg-command-item');
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        this.selectedIndex = Math.min(this.selectedIndex + 1, items.length - 1);
        this.updateSelection(items);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.selectedIndex = Math.max(this.selectedIndex - 1, 0);
        this.updateSelection(items);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const cmd = items[this.selectedIndex]?.dataset.commandId;
        if (cmd) this.execute(cmd);
      }
    }

    updateSelection(items) {
      items.forEach((item, i) => {
        item.classList.toggle('selected', i === this.selectedIndex);
      });
      items[this.selectedIndex]?.scrollIntoView({ block: 'nearest' });
    }

    execute(commandId) {
      this.close();
      if (commandId === 'run') {
        window.PhoenixGuardShortcuts.runAnalysis();
      } else if (commandId === 'save-preset') {
        window.PhoenixGuardShortcuts.saveAsPreset();
      } else if (commandId === 'theme-light') {
        window.PhoenixGuardState?.setTheme('light');
      } else if (commandId === 'theme-dark') {
        window.PhoenixGuardState?.setTheme('dark');
      } else if (commandId === 'help') {
        window.PhoenixGuardShortcuts.toggleHelp();
      } else if (commandId === 'clear-all') {
        window.PhoenixGuardState?.clearAll();
      } else if (commandId.startsWith('preset-')) {
        const presetName = commandId.replace('preset-', '');
        const params = window.PhoenixGuardState?.loadPreset(presetName);
        if (params) {
          window.PhoenixGuardShortcuts.showToast(`Loaded preset: ${presetName}`, 'success');
        }
      }
    }

    open() {
      this.isOpen = true;
      this.overlay.style.display = 'flex';
      this.input.value = '';
      this.searchQuery = '';
      this.search('');
      this.input.focus();
    }

    close() {
      this.isOpen = false;
      this.overlay.style.display = 'none';
    }
  }

  window.PhoenixGuardCommandPalette = new CommandPalette();
  console.log('[PhoenixGuard] Command Palette initialized');
})();

/* ============================================================================
   THEME SWITCHER UI
   ============================================================================ */

(function() {
  'use strict';

  class ThemeSwitcher {
    constructor() {
      this.setupDOM();
      this.attachListeners();
    }

    setupDOM() {
      const html = `
        <div id="pg-theme-switcher" class="pg-theme-switcher" style="position:fixed;bottom:20px;left:20px;z-index:999;">
          <button id="pg-theme-toggle-btn" class="pg-theme-toggle" title="Toggle theme (Ctrl+T)">
            <span id="pg-theme-icon">🌙</span>
          </button>
          <div id="pg-theme-panel" class="pg-theme-panel" style="display:none;">
            <div class="pg-theme-section">
              <label>Theme:</label>
              <button id="pg-theme-light" class="pg-theme-btn" data-theme="light">☀️ Light</button>
              <button id="pg-theme-dark" class="pg-theme-btn pg-theme-btn-active" data-theme="dark">🌙 Dark</button>
            </div>
            <div class="pg-theme-section">
              <label>Accent Color:</label>
              <div class="pg-accent-grid">
                <button class="pg-accent-btn pg-accent-btn-active" data-accent="blue" style="background:#3b82f6;"></button>
                <button class="pg-accent-btn" data-accent="purple" style="background:#a78bfa;"></button>
                <button class="pg-accent-btn" data-accent="emerald" style="background:#10b981;"></button>
                <button class="pg-accent-btn" data-accent="amber" style="background:#f59e0b;"></button>
                <button class="pg-accent-btn" data-accent="rose" style="background:#f43f5e;"></button>
                <button class="pg-accent-btn" data-accent="cyan" style="background:#06b6d4;"></button>
              </div>
            </div>
          </div>
        </div>
      `;
      document.body.insertAdjacentHTML('beforeend', html);
      this.setupStyles();
      this.attachListeners();
    }

    setupStyles() {
      const style = document.createElement('style');
      style.textContent = `
        .pg-theme-switcher { position: fixed; bottom: 20px; left: 20px; z-index: 999; }
        .pg-theme-toggle { width: 48px; height: 48px; border-radius: 50%; background: var(--pg-primary); color: white; border: none; cursor: pointer; font-size: 24px; box-shadow: var(--pg-shadow-lg); transition: var(--pg-transition); }
        .pg-theme-toggle:hover { transform: scale(1.1); }
        .pg-theme-panel { position: absolute; bottom: 60px; left: 0; background: var(--pg-surface); border: 1px solid var(--pg-border); border-radius: 8px; padding: 16px; box-shadow: var(--pg-shadow-xl); min-width: 200px; animation: slideInUp 0.3s ease-out; }
        .pg-theme-section { margin-bottom: 12px; }
        .pg-theme-section label { display: block; font-size: 12px; font-weight: 600; margin-bottom: 8px; color: var(--pg-on-surface); }
        .pg-theme-btn { padding: 6px 12px; border: 1px solid var(--pg-border); background: var(--pg-surface-variant); color: var(--pg-on-surface); border-radius: 4px; cursor: pointer; transition: var(--pg-transition); margin-right: 4px; }
        .pg-theme-btn:hover { background: var(--pg-border); }
        .pg-theme-btn-active { background: var(--pg-primary); color: white; border-color: var(--pg-primary); }
        .pg-accent-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
        .pg-accent-btn { width: 30px; height: 30px; border-radius: 50%; border: 2px solid transparent; cursor: pointer; transition: var(--pg-transition); }
        .pg-accent-btn:hover { transform: scale(1.15); }
        .pg-accent-btn-active { border-color: white; box-shadow: 0 0 0 2px var(--pg-on-surface); }
      `;
      document.head.appendChild(style);
    }

    attachListeners() {
      const btn = document.getElementById('pg-theme-toggle-btn');
      const panel = document.getElementById('pg-theme-panel');
      btn.addEventListener('click', () => {
        panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
      });

      document.querySelectorAll('.pg-theme-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const theme = e.target.dataset.theme;
          window.PhoenixGuardState?.setTheme(theme);
          document.querySelectorAll('.pg-theme-btn').forEach(b => b.classList.remove('pg-theme-btn-active'));
          e.target.classList.add('pg-theme-btn-active');
          document.getElementById('pg-theme-icon').textContent = theme === 'light' ? '☀️' : '🌙';
        });
      });

      document.querySelectorAll('.pg-accent-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const accent = e.target.dataset.accent;
          window.PhoenixGuardState?.setAccentColor(accent);
          document.querySelectorAll('.pg-accent-btn').forEach(b => b.classList.remove('pg-accent-btn-active'));
          e.target.classList.add('pg-accent-btn-active');
        });
      });

      // Close panel on outside click
      document.addEventListener('click', (e) => {
        if (!e.target.closest('#pg-theme-switcher')) {
          panel.style.display = 'none';
        }
      });
    }
  }

  window.PhoenixGuardThemeSwitcher = new ThemeSwitcher();
  console.log('[PhoenixGuard] Theme Switcher initialized');
})();
