/* PhoenixGuard Metric Animator - Real-time metric streaming */

(function() {
  'use strict';

  class MetricAnimator {
    constructor() {
      this.observers = new Map();
      this.setupMutationObserver();
    }

    setupMutationObserver() {
      const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          if (mutation.type === 'childList' || mutation.type === 'characterData') {
            this.animateUpdatedElements(mutation.target);
          }
        });
      });

      // Observe results container
      document.addEventListener('DOMContentLoaded', () => {
        const container = document.querySelector('[data-testid="result_state"]') ||
                         document.querySelector('.pg-reference-main');
        if (container) {
          observer.observe(container, {
            childList: true,
            subtree: true,
            characterData: true,
          });
        }
      });
    }

    animateUpdatedElements(target) {
      // Find gauge elements and animate them
      const gauges = target.querySelectorAll('[data-gauge], .pg-gauge');
      gauges.forEach(gauge => this.animateGauge(gauge));

      // Find counters and animate them
      const counters = target.querySelectorAll('[data-counter], .pg-counter');
      counters.forEach(counter => this.animateCounter(counter));

      // Find progress bars and animate them
      const bars = target.querySelectorAll('[data-progress], .pg-progress-bar');
      bars.forEach(bar => this.animateProgressBar(bar));

      // Stagger animation for lists
      const items = target.querySelectorAll('[data-item], .pg-result-item');
      items.forEach((item, i) => {
        item.style.animationDelay = `${i * 60}ms`;
        item.classList.add('pg-stagger-item');
      });
    }

    animateGauge(gauge) {
      const value = parseFloat(gauge.dataset.value) || 0;
      const duration = 1000; // 1 second
      const start = parseFloat(gauge.textContent) || 0;
      const startTime = Date.now();

      const animate = () => {
        const elapsed = Date.now() - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const current = start + (value - start) * progress;
        gauge.textContent = current.toFixed(2);

        if (progress < 1) {
          requestAnimationFrame(animate);
        }
      };

      animate();
    }

    animateCounter(counter) {
      const value = parseInt(counter.dataset.value) || 0;
      const start = parseInt(counter.textContent) || 0;
      const duration = 600;
      const startTime = Date.now();

      counter.classList.add('pg-counter');
      const valueSpan = counter.querySelector('.pg-counter-value') || counter;

      const animate = () => {
        const elapsed = Date.now() - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const current = Math.round(start + (value - start) * progress);
        valueSpan.textContent = current;

        if (progress < 1) {
          requestAnimationFrame(animate);
        } else {
          valueSpan.classList.add('pg-counter-value');
        }
      };

      animate();
    }

    animateProgressBar(bar) {
      const value = parseFloat(bar.dataset.progress) || 0;
      bar.style.width = '0%';
      bar.classList.add('pg-progress-bar');

      requestAnimationFrame(() => {
        bar.style.width = `${value * 100}%`;
        bar.style.transition = 'width 0.6s cubic-bezier(0.34, 1.56, 0.64, 1)';
      });
    }

    // Animate live gate updates (confidence, cross-checks, etc)
    animateGateUpdate(gateElement, passed, total) {
      const percentage = Math.round((passed / total) * 100);
      gateElement.dataset.percentage = percentage;

      const color = passed >= total * 0.8 ? 'var(--pg-success)' :
                   passed >= total * 0.5 ? 'var(--pg-warning)' :
                   'var(--pg-error)';

      gateElement.style.color = color;
      gateElement.classList.add('pg-glow');

      setTimeout(() => {
        gateElement.classList.remove('pg-glow');
      }, 2000);
    }

    // Animate memory recalls as they happen
    animateMemoryRecall(recallElement) {
      recallElement.classList.add('pg-badge', 'pulse');
      setTimeout(() => {
        recallElement.classList.remove('pulse');
      }, 2000);
    }
  }

  window.PhoenixGuardMetricAnimator = new MetricAnimator();
  console.log('[PhoenixGuard] Metric Animator initialized');
})();

/* ============================================================================
   HELP OVERLAY
   ============================================================================ */

(function() {
  'use strict';

  class HelpOverlay {
    constructor() {
      this.isVisible = false;
      this.setupDOM();
      this.attachListeners();
    }

    setupDOM() {
      const shortcuts = window.PhoenixGuardShortcuts?.getShortcuts() || [];
      const shortcutHtml = shortcuts.map(s => `
        <div class="pg-help-item">
          <div class="pg-help-keys">${s.keys}</div>
          <div class="pg-help-desc">${s.title}</div>
        </div>
      `).join('');

      const html = `
        <div id="pg-help-overlay-bg" class="pg-modal-overlay" style="display:none;"></div>
        <div id="pg-help-overlay" class="pg-help-overlay pg-modal" style="display:none;">
          <div class="pg-help-header">
            <h2>Keyboard Shortcuts</h2>
            <button id="pg-help-close" class="pg-help-close">✕</button>
          </div>
          <div class="pg-help-content">
            <div class="pg-help-section">
              <h3>Navigation</h3>
              ${shortcutHtml.split('</div>').slice(0, 4).join('</div>') + '</div>'}
            </div>
            <div class="pg-help-section">
              <h3>Actions</h3>
              ${shortcutHtml.split('</div>').slice(4).join('</div>') + '</div>'}
            </div>
            <div class="pg-help-tips">
              <h3>💡 Tips</h3>
              <ul>
                <li>Save your favorite parameter combinations as presets</li>
                <li>Switch themes and accent colors without losing your work</li>
                <li>Use keyboard shortcuts for faster workflows</li>
                <li>All preferences are saved automatically</li>
              </ul>
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
        .pg-help-overlay { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 90%; max-width: 600px; max-height: 80vh; overflow-y: auto; z-index: 10001; border-radius: 8px; }
        .pg-help-header { display: flex; justify-content: space-between; align-items: center; padding: 20px; border-bottom: 1px solid var(--pg-border); position: sticky; top: 0; background: var(--pg-surface); }
        .pg-help-header h2 { margin: 0; }
        .pg-help-close { background: none; border: none; font-size: 24px; cursor: pointer; color: var(--pg-on-surface); }
        .pg-help-content { padding: 20px; }
        .pg-help-section { margin-bottom: 20px; }
        .pg-help-section h3 { margin: 0 0 12px 0; color: var(--pg-primary); }
        .pg-help-item { display: flex; gap: 16px; margin-bottom: 8px; padding: 8px; border-radius: 4px; background: var(--pg-surface-variant); }
        .pg-help-keys { font-family: monospace; background: var(--pg-primary); color: white; padding: 4px 8px; border-radius: 3px; white-space: nowrap; font-size: 12px; font-weight: 600; }
        .pg-help-desc { color: var(--pg-on-surface); }
        .pg-help-tips { background: var(--pg-accent-light); padding: 12px; border-radius: 4px; margin-top: 20px; }
        .pg-help-tips ul { margin: 8px 0; padding-left: 20px; }
        .pg-help-tips li { margin: 4px 0; }
      `;
      document.head.appendChild(style);
    }

    attachListeners() {
      const bg = document.getElementById('pg-help-overlay-bg');
      const close = document.getElementById('pg-help-close');

      window.addEventListener('pg:toggleHelp', (e) => {
        if (e.detail.visible) this.show();
        else this.hide();
      });

      close?.addEventListener('click', () => this.hide());
      bg?.addEventListener('click', () => this.hide());

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.isVisible) {
          this.hide();
        }
      });
    }

    show() {
      this.isVisible = true;
      document.getElementById('pg-help-overlay-bg').style.display = 'block';
      document.getElementById('pg-help-overlay').style.display = 'block';
      document.getElementById('pg-help-overlay').style.animation = 'fadeInUp 0.3s ease-out';
    }

    hide() {
      this.isVisible = false;
      document.getElementById('pg-help-overlay-bg').style.display = 'none';
      document.getElementById('pg-help-overlay').style.display = 'none';
    }
  }

  window.PhoenixGuardHelpOverlay = new HelpOverlay();
  console.log('[PhoenixGuard] Help Overlay initialized');
})();
