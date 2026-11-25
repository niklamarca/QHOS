const RESEARCH_SCREENS = new Set(['pipeline', 'quotes', 'integrity', 'state', 'liquidity', 'queue', 'mbo', 'experiment', 'events']);
const LEGACY_SCREENS = { 'micro-overview': 'state', 'micro-book': 'liquidity', 'micro-events': 'mbo', 'micro-pipeline': 'pipeline', 'micro-studies': 'events', microstructurePanel: 'state', executionHistorySection: 'history' };

/** Screen navigation keeps the terminal, imported data and research forms mounted. */
export class ScreenNavigation {
  constructor({ onTerminal, onResearch, onHistory, onPane } = {}) {
    this.callbacks = { onTerminal, onResearch, onHistory, onPane };
    this.screen = 'terminal';
    this.researchScroll = new Map();
    this.app = document.querySelector('#terminalApp');
    this.nav = document.querySelector('#screenNav');
    this.viewport = document.querySelector('#screenViewport');
    this.menu = document.querySelector('#screenMenuButton');
    this.backdrop = document.querySelector('#screenNavBackdrop');
    this.terminal = document.querySelector('#terminalScreen');
    this.research = document.querySelector('#microstructurePanel');
    this.history = document.querySelector('#executionHistorySection');
    this.compact = matchMedia('(max-width: 1100px)');
    this.mobile = matchMedia('(max-width: 1000px)');
    this.drawerOpen = false;
    document.querySelectorAll('[data-screen-target]').forEach(button => button.addEventListener('click', event => {
      event.preventDefault();
      this.show(button.dataset.screenTarget);
    }));
    this.menu.addEventListener('click', () => this.setDrawer(!this.drawerOpen));
    document.querySelector('#screenMenuClose').addEventListener('click', () => this.setDrawer(false, true));
    this.backdrop.addEventListener('click', () => this.setDrawer(false, true));
    this.compact.addEventListener('change', () => this.setDrawer(false));
    this.mobile.addEventListener('change', () => this.callbacks.onPane?.());
    document.querySelectorAll('button[data-terminal-pane]').forEach(button => button.addEventListener('click', () => this.setPane(button.dataset.terminalPane)));
    document.querySelector('.compact-workspace-bar').addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const buttons = [...document.querySelectorAll('button[data-terminal-pane]')];
      const index = buttons.indexOf(event.target);
      if (index < 0) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      this.setPane(buttons[next].dataset.terminalPane);
      buttons[next].focus();
    });
    document.addEventListener('keydown', event => {
      if (!this.drawerOpen) return;
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopImmediatePropagation();
        this.setDrawer(false, true);
      } else if (event.key === 'Tab') {
        const buttons = [...this.nav.querySelectorAll('button')].filter(el => !el.disabled && el.getClientRects().length);
        const first = buttons[0], last = buttons.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    });
    window.addEventListener('popstate', () => this.show(this.fromHash(), { updateURL: false }));
    this.setDrawer(false);
  }

  fromHash() {
    const key = location.hash.slice(1);
    return RESEARCH_SCREENS.has(key) || key === 'history' ? key : LEGACY_SCREENS[key] ?? 'terminal';
  }

  setDrawer(open, restoreFocus = false) {
    this.drawerOpen = this.compact.matches && open;
    this.app.classList.toggle('nav-open', this.drawerOpen);
    this.menu.setAttribute('aria-expanded', String(this.drawerOpen));
    this.menu.setAttribute('aria-label', this.drawerOpen ? 'Fechar menu de telas' : 'Abrir menu de telas');
    this.backdrop.hidden = !this.drawerOpen;
    this.nav.inert = this.compact.matches && !this.drawerOpen;
    this.viewport.inert = this.drawerOpen;
    if (this.drawerOpen) {
      this.nav.setAttribute('role', 'dialog');
      this.nav.setAttribute('aria-modal', 'true');
      this.nav.querySelector('[aria-current="page"]')?.focus({ preventScroll: true });
    } else {
      this.nav.removeAttribute('role');
      this.nav.removeAttribute('aria-modal');
      if (restoreFocus) this.menu.focus({ preventScroll: true });
    }
  }

  setPane(pane) {
    if (!['chart', 'analysis', 'book'].includes(pane)) return;
    this.terminal.dataset.terminalPane = pane;
    document.querySelectorAll('button[data-terminal-pane]').forEach(button => {
      const active = button.dataset.terminalPane === pane;
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
    });
    this.callbacks.onPane?.(pane);
  }

  show(key, { focus = true, updateURL = true, pane } = {}) {
    const screen = RESEARCH_SCREENS.has(key) || key === 'history' ? key : 'terminal';
    const previous = this.screen;
    if (RESEARCH_SCREENS.has(previous)) this.researchScroll.set(previous, this.research.scrollTop);
    this.screen = screen;
    this.terminal.hidden = screen !== 'terminal';
    this.research.hidden = !RESEARCH_SCREENS.has(screen);
    this.history.hidden = screen !== 'history';
    this.app.dataset.screen = screen;
    document.querySelectorAll('.screen-nav-item').forEach(button => {
      const active = button.dataset.screenTarget === screen || (screen === 'history' && button.dataset.screenTarget === 'terminal');
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
    });
    document.querySelector('[data-view="charts"]')?.classList.toggle('active', screen === 'terminal' || screen === 'history');
    this.setDrawer(false);
    let heading;
    if (screen === 'terminal') {
      if (pane) this.setPane(pane);
      this.callbacks.onTerminal?.();
      heading = this.mobile.matches && this.terminal.dataset.terminalPane !== 'chart'
        ? document.querySelector(`button[data-terminal-pane="${this.terminal.dataset.terminalPane}"]`)
        : document.querySelector('#chartContainer canvas[tabindex="0"]');
    } else if (screen === 'history') {
      this.callbacks.onHistory?.();
      heading = document.querySelector('#executionHistoryTitle');
    } else {
      this.callbacks.onResearch?.(screen);
      this.research.scrollTop = this.researchScroll.get(screen) ?? 0;
      heading = document.querySelector('#microScreenTitle');
    }
    if (updateURL && previous !== screen) window.history.pushState(null, '', `#${screen}`);
    if (focus) heading?.focus({ preventScroll: true });
  }

  start() { this.show(this.fromHash(), { focus: false, updateURL: false }); }
}
