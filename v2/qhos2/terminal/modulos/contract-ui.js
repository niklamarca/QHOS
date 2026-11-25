import {icon} from 'qhos-module/icons.js';

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const number = value => Number.isFinite(value) ? value.toLocaleString('pt-BR', {maximumFractionDigits:10}) : '—';
const sourceName = provider => provider === 'rithmic' ? 'Rithmic · QHOS' : 'Coinbase';
const errorText = error => String(error?.message || error || 'Não foi possível concluir. Tente novamente.').slice(0,400);
const expiryText = value => /^\d{4}-\d{2}-\d{2}$/.test(value ?? '') ? value.split('-').reverse().join('/') : 'Sem vencimento';
const availableConnection = connection => ['waiting','live','stale','synchronized'].includes(connection?.state) && !!connection?.selection;

/**
 * Contract choices are drafts. Only applySelection can change the dashboard.
 * This class never invents contracts, subscriptions, source status or prices.
 */
export class ContractManagerUI {
  constructor({openModal, closeModal, getState, search, applySelection, connect, disconnect, useCoinbase, notify} = {}) {
    Object.assign(this, {openModal, closeModal, getState, search, applySelection, connect, disconnect, useCoinbase, notify});
    this.mode = null;
    this.root = null;
    this.provider = 'coinbase';
    this.epoch = 0;
    this.searchSequence = 0;
    this.searchTimer = null;
    this.searchAbort = null;
    this.cache = new Map();
    this.drafts = new Map();
    this.results = [];
    this.pending = false;
    this.connecting = false;
    this.replacing = null;
    this.inspectId = null;
    this.query = '';
    this.lastStatusHTML = '';
  }

  state() { return this.getState?.() ?? {}; }
  $(selector) { return this.root?.querySelector(selector) ?? null; }
  active() { return !!this.root?.isConnected && !this.root.closest('#modalBackdrop')?.hidden; }
  limit() { return Math.max(1, Math.min(3, Number(this.state().maxInstruments) || 3)); }
  draft() { return this.drafts.get(this.provider) ?? {ids:[], activeId:null, dirty:false}; }

  remember(instruments) {
    for (const instrument of Array.isArray(instruments) ? instruments : []) {
      if (instrument && typeof instrument.id === 'string' && instrument.id) this.cache.set(instrument.id, instrument);
    }
  }

  confirmed(provider = this.provider) {
    const state = this.state();
    this.remember(state.instruments);
    this.remember(state.connection?.selection?.instruments);
    if (provider === state.provider) return {ids:[...(state.watchlistIds ?? [])], activeId:state.activeId ?? null};
    if (provider === 'rithmic' && state.connection?.selection) {
      const selection = state.connection.selection;
      return {ids:(selection.instruments ?? []).map(item => item.id), activeId:selection.activeId ?? null};
    }
    return {ids:[], activeId:null};
  }

  initializeDraft(provider, force = false) {
    if (this.drafts.has(provider) && !force) return;
    const confirmed = this.confirmed(provider), ids = confirmed.ids.filter(id => this.cache.has(id)).slice(0, this.limit());
    this.drafts.set(provider, {ids, activeId:ids.includes(confirmed.activeId) ? confirmed.activeId : ids[0] ?? null, dirty:false});
  }

  cancelSearch() {
    clearTimeout(this.searchTimer);
    this.searchTimer = null;
    this.searchAbort?.abort();
    this.searchAbort = null;
    this.searchSequence++;
  }

  close() {
    this.cancelSearch();
    const token = this.$('#qhosAccessToken');
    if (token) token.value = '';
    this.epoch++;
    this.root = null;
    this.mode = null;
    this.connecting = false;
    this.lastStatusHTML = '';
    this.lastActiveIdentity = null;
  }

  destroy() { this.close(); this.cache.clear(); this.drafts.clear(); }

  open({provider, preserveDraft = false} = {}) {
    this.close();
    this.mode = 'contracts';
    this.pending = false;
    this.provider = ['coinbase','rithmic'].includes(provider) ? provider : this.state().provider === 'rithmic' ? 'rithmic' : 'coinbase';
    if (!preserveDraft) this.drafts.clear();
    this.initializeDraft(this.provider, !preserveDraft || !this.drafts.get(this.provider)?.dirty);
    this.replacing = null;
    this.query = '';
    this.results = [];
    this.inspectId = this.draft().activeId;
    this.openModal('Seu mercado, na sua tela.', `<div class="contract-manager" id="contractManager">
      <div class="contract-source-tabs" role="tablist" aria-label="Fonte do catálogo">
        <button type="button" id="contractSourceCoinbase" role="tab" data-contract-source="coinbase" aria-controls="contractCatalogPanel">Coinbase <span>Spot</span></button>
        <button type="button" id="contractSourceRithmic" role="tab" data-contract-source="rithmic" aria-controls="contractCatalogPanel">Rithmic <span>QHOS</span></button>
      </div>
      <div id="contractSourceStatus" class="contract-source-status" role="status" aria-live="polite"></div>
      <section id="contractCatalogPanel" role="tabpanel">
        <div id="contractDisconnected" class="contract-connect-empty" hidden>
          <span class="contract-empty-icon">${icon('layers')}</span>
          <h3>Conecte o coletor QHOS.</h3>
          <p>Os contratos disponíveis e seus vencimentos virão do serviço de dados da sua plataforma.</p>
          <button type="button" class="contract-button accent" data-contract-connect>${icon('settings')} Configurar conexão</button>
          <small>Ao conectar, a tela acompanha a seleção confirmada pelo coletor.</small>
        </div>
        <div id="contractWorkspace" class="contract-layout">
          <div class="contract-catalog">
            <label class="contract-search" for="contractSearch">${icon('search')}<input id="contractSearch" type="search" maxlength="100" placeholder="Buscar ativo ou contrato…" autocomplete="off" spellcheck="false" aria-label="Buscar ativo ou contrato" aria-controls="contractResults"></label>
            <div id="contractCatalogHint" class="contract-catalog-hint" role="status" aria-live="polite"></div>
            <div class="contract-results" id="contractResults" aria-label="Resultados da busca"></div>
            <div class="contract-specs" id="contractSpecs" aria-live="polite"></div>
          </div>
          <aside class="contract-selection" aria-labelledby="contractSelectionHeading">
            <div class="contract-section-heading"><h3 id="contractSelectionHeading">Sua seleção</h3><span id="contractSelectionCount"></span></div>
            <p class="contract-selection-help">Escolha o contrato que ficará no gráfico.</p>
            <div id="contractSlots" class="contract-slots"></div>
            <div id="contractReplaceHint" class="contract-replace-hint" hidden></div>
            <p class="contract-draft-note">As alterações são aplicadas juntas.</p>
          </aside>
        </div>
      </section>
      <div id="contractSelectionError" class="contract-error" role="alert" hidden></div>
      <div class="contract-apply-row" id="contractApplyRow">
        <div><span class="contract-apply-label">NO DASHBOARD</span><p id="contractCurrentSelection"></p></div>
        <button type="button" class="contract-button accent" id="contractApply">Aplicar seleção ${icon('arrow-right')}</button>
      </div>
      <div class="contract-pending" id="contractPending" role="status" aria-live="polite" hidden></div>
    </div>`, {wide:true, name:'contracts', eyebrow:'ATIVOS & CONTRATOS'});
    this.root = document.getElementById('contractManager');
    this.bindManager();
    this.renderProvider();
    this.renderDraft();
    this.update();
    this.runSearch();
    const epoch = this.epoch;
    requestAnimationFrame(() => {
      if (!this.active() || epoch !== this.epoch) return;
      if (this.$('#contractWorkspace').hidden) this.$('#contractDisconnected [data-contract-connect]')?.focus();
      else this.$('#contractSearch')?.focus();
    });
  }

  bindManager() {
    this.root.addEventListener('click', event => {
      const button = event.target.closest('button');
      if (!button || button.disabled) return;
      if (button.dataset.contractSource) this.switchProvider(button.dataset.contractSource);
      else if (button.hasAttribute('data-contract-connect')) this.openConnection();
      else if (button.dataset.contractResult) this.choose(button.dataset.contractResult);
      else if (button.dataset.contractFocus) this.setActive(button.dataset.contractFocus);
      else if (button.dataset.contractReplace) this.startReplacement(button.dataset.contractReplace);
      else if (button.dataset.contractRemove) this.remove(button.dataset.contractRemove);
      else if (button.hasAttribute('data-contract-empty-slot')) { this.replacing = null; this.renderDraft(); this.$('#contractSearch')?.focus(); }
      else if (button.hasAttribute('data-contract-replace-cancel')) { this.replacing = null; this.renderDraft(); this.renderResults(); }
      else if (button.hasAttribute('data-contract-retry')) this.runSearch();
      else if (button.id === 'contractApply') this.apply();
    });
    this.$('#contractSearch').addEventListener('input', event => {
      this.query = event.target.value;
      this.cancelSearch();
      this.results = [];
      this.renderResults();
      this.$('#contractCatalogHint').textContent = 'Buscando contratos…';
      this.searchTimer = setTimeout(() => this.runSearch(), 220);
    });
    this.$('#contractSearch').addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') { event.preventDefault(); this.$('[data-contract-result]')?.focus(); }
      if (event.key === 'Enter') { event.preventDefault(); this.$('[data-contract-result]')?.click(); }
    });
    this.root.addEventListener('keydown', event => {
      const tab = event.target.closest('[data-contract-source]');
      if (tab && ['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {
        event.preventDefault();
        if (this.pending) return;
        const next = event.key === 'Home' ? 'coinbase' : event.key === 'End' ? 'rithmic' : this.provider === 'coinbase' ? 'rithmic' : 'coinbase';
        this.switchProvider(next);
        this.$(`[data-contract-source="${next}"]`)?.focus();
      }
      const result = event.target.closest('[data-contract-result]');
      if (result && ['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
        event.preventDefault();
        const buttons = [...this.root.querySelectorAll('[data-contract-result]')], current = buttons.indexOf(result);
        const index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : Math.max(0, Math.min(buttons.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1)));
        buttons[index]?.focus();
      }
    });
  }

  switchProvider(provider) {
    if (this.pending || this.provider === provider) return;
    this.cancelSearch();
    this.provider = provider;
    this.initializeDraft(provider);
    this.query = '';
    this.$('#contractSearch').value = '';
    this.results = [];
    this.replacing = null;
    this.inspectId = this.draft().activeId;
    this.clearError();
    this.renderProvider();
    this.renderDraft();
    this.update();
    this.runSearch();
  }

  renderProvider() {
    for (const tab of this.root.querySelectorAll('[data-contract-source]')) {
      const selected = tab.dataset.contractSource === this.provider;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
    this.$('#contractCatalogPanel').setAttribute('aria-labelledby', this.provider === 'rithmic' ? 'contractSourceRithmic' : 'contractSourceCoinbase');
  }

  async runSearch() {
    this.cancelSearch();
    if (!this.active() || this.mode !== 'contracts') return;
    if (this.provider === 'rithmic' && !availableConnection(this.state().connection)) {
      this.results = []; this.renderResults(); return;
    }
    const epoch = this.epoch, sequence = this.searchSequence, provider = this.provider;
    this.searchAbort = new AbortController();
    this.$('#contractCatalogHint').textContent = 'Buscando contratos…';
    this.$('#contractResults').setAttribute('aria-busy', 'true');
    try {
      const instruments = await this.search(this.query.trim(), {provider, signal:this.searchAbort.signal});
      if (!this.active() || epoch !== this.epoch || sequence !== this.searchSequence || provider !== this.provider) return;
      this.results = Array.isArray(instruments) ? instruments.slice(0,100) : [];
      this.remember(this.results);
      this.renderResults();
      this.renderSpecs();
    } catch (error) {
      if (error?.name === 'AbortError' || !this.active() || epoch !== this.epoch || sequence !== this.searchSequence) return;
      this.results = [];
      this.$('#contractCatalogHint').textContent = 'Busca indisponível';
      this.$('#contractResults').innerHTML = `<div class="contract-results-empty"><strong>Não foi possível buscar o catálogo.</strong><p>${esc(errorText(error))}</p><button type="button" class="contract-button" data-contract-retry>Tentar novamente</button></div>`;
    } finally {
      if (this.active() && epoch === this.epoch && sequence === this.searchSequence) this.$('#contractResults')?.setAttribute('aria-busy', 'false');
    }
  }

  renderResults() {
    if (!this.active() || this.mode !== 'contracts') return;
    const draft = this.draft(), state = this.state(), replacement = this.cache.get(this.replacing);
    this.$('#contractCatalogHint').textContent = replacement ? `Escolha o substituto de ${replacement.symbol}` : `${this.results.length} ${this.results.length === 1 ? 'resultado' : 'resultados'} · ${sourceName(this.provider)}`;
    const resultRoot = this.$('#contractResults');
    resultRoot.innerHTML = this.results.length ? this.results.map(instrument => {
      const selected = draft.ids.includes(instrument.id), focused = instrument.id === draft.activeId;
      const actual = state.provider === this.provider && state.activeId === instrument.id;
      const detail = `${instrument.exchange} · ${instrument.expiry ? 'Venc. '+expiryText(instrument.expiry) : instrument.assetClass === 'spot' ? 'Mercado spot' : 'Sem vencimento'}`;
      const action = actual ? 'No gráfico' : selected ? focused ? 'Após aplicar' : 'Selecionado' : replacement ? 'Substituir' : 'Adicionar';
      return `<button type="button" class="contract-result ${selected ? 'selected' : ''}" data-contract-result="${esc(instrument.id)}" aria-pressed="${selected}" ${this.pending ? 'disabled' : ''} aria-label="${esc(`${instrument.symbol}, ${instrument.name}, ${detail}. ${action}`)}">
        <span class="contract-monogram" aria-hidden="true">${esc((instrument.symbol || '?').slice(0,2))}</span>
        <span class="contract-result-main"><strong>${esc(instrument.symbol)}${actual ? '<i class="contract-current-dot" title="Contrato atual no dashboard"></i>' : ''}</strong><span>${esc(instrument.name)}</span><small>${esc(detail)}</small></span>
        <span class="contract-result-action">${selected ? icon('check') : replacement ? icon('reset') : icon('plus')}<span>${action}</span></span>
      </button>`;
    }).join('') : `<div class="contract-results-empty">${icon('search')}<strong>Nenhum contrato encontrado.</strong><p>${this.query ? 'Experimente outro nome ou símbolo.' : 'Busque pelo nome ou símbolo do contrato.'}</p></div>`;
    resultRoot.setAttribute('aria-busy', 'false');
  }

  choose(id) {
    if (this.pending || !this.cache.has(id)) return;
    const draft = this.draft();
    this.clearError();
    if (draft.ids.includes(id)) {
      draft.activeId = id;
      this.replacing = null;
    } else if (this.replacing && draft.ids.includes(this.replacing)) {
      const previous = this.replacing, index = draft.ids.indexOf(previous);
      draft.ids[index] = id;
      if (draft.activeId === previous) draft.activeId = id;
      this.replacing = null;
    } else if (draft.ids.length < this.limit()) {
      draft.ids.push(id);
      draft.activeId ||= id;
    } else {
      this.showError('Sua seleção está completa. Clique em “Substituir” no contrato que deseja trocar.');
      this.inspectId = id;
      this.renderSpecs();
      this.$('[data-contract-replace]')?.focus();
      return;
    }
    draft.dirty = true;
    this.inspectId = id;
    this.renderDraft();
    this.renderResults();
    this.updateApply();
    [...this.root.querySelectorAll('[data-contract-result]')].find(button => button.dataset.contractResult === id)?.focus();
  }

  setActive(id) {
    const draft = this.draft();
    if (this.pending || !draft.ids.includes(id)) return;
    draft.activeId = id; draft.dirty = true;
    this.inspectId = id;
    this.clearError(); this.renderDraft(); this.renderResults(); this.updateApply();
    [...this.root.querySelectorAll('[data-contract-focus]')].find(button => button.dataset.contractFocus === id)?.focus();
  }

  startReplacement(id) {
    if (this.pending) return;
    this.replacing = id;
    this.inspectId = id;
    this.clearError(); this.renderDraft(); this.renderResults();
    this.$('#contractSearch').focus();
    this.$('#contractSearch').select();
  }

  remove(id) {
    const draft = this.draft();
    if (this.pending) return;
    draft.ids = draft.ids.filter(value => value !== id);
    if (draft.activeId === id) draft.activeId = draft.ids[0] ?? null;
    if (this.replacing === id) this.replacing = null;
    draft.dirty = true; this.inspectId = draft.activeId;
    this.clearError(); this.renderDraft(); this.renderResults(); this.updateApply();
    this.$('#contractSearch')?.focus();
  }

  renderDraft() {
    const draft = this.draft(), state = this.state();
    this.$('#contractSelectionCount').textContent = `${draft.ids.length} / ${this.limit()}`;
    this.$('#contractSlots').innerHTML = Array.from({length:this.limit()}, (_, index) => {
      const id = draft.ids[index], item = this.cache.get(id);
      if (!item) return `<button type="button" class="contract-empty-slot" data-contract-empty-slot ${this.pending ? 'disabled' : ''}>${icon('plus')}<span>Adicionar contrato</span></button>`;
      const active = id === draft.activeId;
      const actual = state.provider === this.provider && state.activeId === id;
      return `<article class="contract-slot ${active ? 'active' : ''} ${id === this.replacing ? 'replacing' : ''}">
        <button type="button" class="contract-slot-focus" data-contract-focus="${esc(id)}" aria-pressed="${active}" aria-label="Mostrar ${esc(item.symbol)} no gráfico após aplicar" ${this.pending ? 'disabled' : ''}>
          <span class="contract-radio" aria-hidden="true"></span><span><strong>${esc(item.symbol)}</strong><small>${esc(item.exchange)}${item.expiry ? ' · '+esc(expiryText(item.expiry)) : ''}</small></span><em>${actual ? 'No gráfico' : active ? 'Após aplicar' : ''}</em>
        </button>
        <div class="contract-slot-actions"><button type="button" data-contract-replace="${esc(id)}" aria-label="Substituir ${esc(item.symbol)}" ${this.pending ? 'disabled' : ''}>${icon('reset')} Substituir</button><button type="button" data-contract-remove="${esc(id)}" aria-label="Remover ${esc(item.symbol)} da seleção" ${this.pending ? 'disabled' : ''}>${icon('close')}<span>Remover</span></button></div>
      </article>`;
    }).join('');
    const replacement = this.cache.get(this.replacing);
    this.$('#contractReplaceHint').hidden = !replacement;
    this.$('#contractReplaceHint').innerHTML = replacement ? `Substituindo <strong>${esc(replacement.symbol)}</strong>. Escolha outro contrato na busca.<button type="button" data-contract-replace-cancel>Cancelar troca</button>` : '';
    this.renderSpecs();
  }

  renderSpecs() {
    const item = this.cache.get(this.inspectId) ?? this.cache.get(this.draft().activeId);
    const target = this.$('#contractSpecs');
    target.hidden = !item;
    if (!item) { target.innerHTML = ''; return; }
    const capabilities = item.capabilities ?? {};
    target.innerHTML = `<div class="contract-specs-heading"><strong>${esc(item.symbol)}</strong><span>Especificações do contrato</span></div>
      <dl><div><dt>Tick mínimo</dt><dd>${number(item.tickSize)} ${esc(item.currency)}</dd></div><div><dt>Quantidade</dt><dd>${esc(item.quantityUnit)}</dd></div><div><dt>Multiplicador</dt><dd>${number(item.contractMultiplier)}×</dd></div><div><dt>Vencimento</dt><dd>${esc(expiryText(item.expiry))}</dd></div></dl>
      <div class="contract-capabilities" aria-label="Dados disponíveis">${[['quotes','Cotações'],['trades','Negócios'],['depth','Livro L2'],['history','Histórico']].map(([key,label]) => `<span class="${capabilities[key] ? 'available' : ''}">${capabilities[key] ? icon('check') : icon('minus')}${label}</span>`).join('')}</div>`;
  }

  updateApply() {
    if (!this.active() || this.mode !== 'contracts') return;
    const state = this.state(), draft = this.draft(), current = this.confirmed(this.provider);
    const unchanged = state.provider === this.provider && current.activeId === draft.activeId && current.ids.length === draft.ids.length && current.ids.every((id,index) => draft.ids[index] === id);
    const available = this.provider === 'coinbase' || availableConnection(state.connection);
    const button = this.$('#contractApply');
    button.disabled = this.pending || !available || !draft.ids.length || !draft.ids.includes(draft.activeId) || unchanged;
    button.innerHTML = this.pending ? `${icon('clock')} Aguardando…` : unchanged ? `${icon('check')} Seleção atual` : `Aplicar seleção ${icon('arrow-right')}`;
    const actual = this.cache.get(state.activeId);
    this.$('#contractCurrentSelection').textContent = `${actual?.symbol ?? 'Nenhum contrato'} · ${sourceName(state.provider)}`;
    const pending = this.$('#contractPending');
    pending.hidden = !this.pending;
    pending.textContent = this.provider === 'rithmic' ? 'Aguardando confirmação do coletor. A tela só muda após a assinatura ser confirmada.' : 'Aplicando seleção…';
    for (const tab of this.root.querySelectorAll('[data-contract-source]')) tab.disabled = this.pending;
    this.$('#contractSearch').disabled = this.pending;
  }

  showError(message) { const target = this.$('#contractSelectionError'); if (target) { target.textContent = message; target.hidden = false; } }
  clearError() { const target = this.$('#contractSelectionError'); if (target) { target.hidden = true; target.textContent = ''; } }

  async apply() {
    if (this.pending || this.$('#contractApply')?.disabled) return;
    const draft = this.draft(), epoch = this.epoch, provider = this.provider;
    this.pending = true; this.clearError(); this.renderDraft(); this.renderResults(); this.updateApply();
    try {
      await this.applySelection({provider, ids:[...draft.ids], activeId:draft.activeId});
      if (!this.active() || epoch !== this.epoch) return;
      this.notify?.('Seleção confirmada. O dashboard acompanha os contratos escolhidos.');
      this.closeModal();
      this.close();
    } catch (error) {
      if (!this.active() || epoch !== this.epoch) return;
      this.showError(errorText(error));
    } finally {
      if (this.active() && epoch === this.epoch) {
        this.pending = false; this.renderDraft(); this.renderResults(); this.updateApply();
      }
    }
  }

  update() {
    if (!this.active()) return;
    if (this.mode === 'connection') { this.updateConnection(); return; }
    if (this.mode !== 'contracts') return;
    const state = this.state(), connection = state.connection ?? {}, available = availableConnection(connection);
    this.remember(state.instruments); this.remember(connection.selection?.instruments);
    const activeIdentity = `${state.provider ?? ''}:${state.activeId ?? ''}`;
    if (this.lastActiveIdentity !== activeIdentity) {
      this.lastActiveIdentity = activeIdentity;
      this.renderDraft();
      this.renderResults();
    }
    let description, statusClass = '', title;
    if (this.provider === 'coinbase') {
      title = 'Coinbase Exchange'; description = 'Catálogo de mercado spot · dados públicos desta bolsa.';
    } else {
      title = 'Rithmic via QHOS';
      const trying = connection.state === 'connecting' || connection.retrying || connection.autoConnect?.active || connection.autoConnect?.attempting;
      statusClass = connection.state === 'live' ? 'live' : available || trying ? 'ready' : 'disconnected';
      description = connection.state === 'live' ? 'Dados recebidos para a seleção confirmada.' : available ? 'Coletor conectado · aguardando dados ou sua próxima seleção.' : trying ? 'Conectando ao coletor…' : 'Coletor desconectado · nenhum contrato Rithmic será exibido como ao vivo.';
    }
    const html = `<div class="contract-source-summary"><span class="contract-connection-dot ${statusClass}"></span><div><strong>${title}</strong><span>${description}</span></div>${this.provider === 'rithmic' && connection.environment === 'test' ? '<b class="contract-test-badge">TESTE</b>' : ''}</div>${this.provider === 'rithmic' ? `<button type="button" class="contract-source-settings" data-contract-connect ${this.pending ? 'disabled' : ''}>${icon('settings')} Conexão</button>` : ''}`;
    if (this.lastStatusHTML !== html) { this.$('#contractSourceStatus').innerHTML = html; this.lastStatusHTML = html; }
    const disconnected = this.provider === 'rithmic' && !available;
    const workspaceWasHidden = this.$('#contractWorkspace').hidden;
    this.$('#contractDisconnected').hidden = !disconnected;
    this.$('#contractWorkspace').hidden = disconnected;
    this.$('#contractApplyRow').hidden = disconnected;
    if (!disconnected && workspaceWasHidden) {
      if (!this.draft().dirty) { this.initializeDraft(this.provider, true); this.inspectId = this.draft().activeId; this.renderDraft(); }
      this.runSearch();
    }
    this.updateApply();
  }

  openConnection() {
    this.close();
    this.mode = 'connection';
    this.connecting = false;
    const state = this.state(), connection = state.connection ?? {};
    this.openModal('Conexão da sua plataforma.', `<form class="contract-connection-form" id="contractConnectionForm">
      <div id="contractConnectionStatus" class="contract-connection-card" role="status" aria-live="polite"></div>
      <p class="contract-form-intro">Escolha os contratos no dashboard e deixe o coletor QHOS cuidar da recepção dos dados da Rithmic.</p>
      <div class="form-field"><label for="qhosEndpoint">Endereço do coletor QHOS</label><input type="url" id="qhosEndpoint" placeholder="wss://seu-coletor.exemplo" value="${esc(connection.endpoint ?? '')}" maxlength="1000" spellcheck="false" autocomplete="off" required><small>Use o endereço do serviço de dados da sua plataforma.</small></div>
      <div class="form-field"><label for="qhosAccessToken">Token de acesso do QHOS <span>opcional</span></label><input type="password" id="qhosAccessToken" placeholder="Token desta sessão" autocomplete="off" maxlength="4096"><small>O token é usado apenas nesta sessão.</small></div>
      <div id="contractConnectionError" class="contract-error" role="alert" hidden></div>
      <button type="submit" class="contract-button accent contract-connect-submit" id="qhosConnectButton">${icon('arrow-right')} Conectar coletor</button>
      <div class="contract-connection-actions"><button type="button" class="contract-button" id="qhosBackToContracts">Voltar aos contratos</button><button type="button" class="contract-disconnect" id="qhosDisconnectButton">Desconectar coletor</button></div>
      <button type="button" class="contract-coinbase-return" id="qhosUseCoinbase">Usar dados públicos da Coinbase</button>
    </form>`, {name:'contract-connection', eyebrow:'DADOS · QHOS / RITHMIC'});
    this.root = document.getElementById('contractConnectionForm');
    this.root.addEventListener('submit', event => { event.preventDefault(); this.connectCollector(); });
    this.$('#qhosBackToContracts').addEventListener('click', () => this.open({provider:'rithmic', preserveDraft:true}));
    this.$('#qhosDisconnectButton').addEventListener('click', async () => {
      const epoch = this.epoch;
      try { await this.disconnect?.(); if (this.active() && epoch === this.epoch) { this.$('#qhosAccessToken').value = ''; this.updateConnection(); } }
      catch (error) { if (this.active() && epoch === this.epoch) this.connectionError(error); }
    });
    this.$('#qhosUseCoinbase').addEventListener('click', async () => {
      const epoch = this.epoch;
      try { await this.useCoinbase?.(); if (this.active() && epoch === this.epoch) this.open({provider:'coinbase'}); }
      catch (error) { if (this.active() && epoch === this.epoch) this.connectionError(error); }
    });
    this.updateConnection();
  }

  connectionError(error) {
    const target = this.$('#contractConnectionError');
    if (target) { target.textContent = errorText(error); target.hidden = false; }
  }

  async connectCollector() {
    if (this.connecting || !this.active() || !this.root.reportValidity()) return;
    const url = this.$('#qhosEndpoint').value.trim();
    let token = this.$('#qhosAccessToken').value;
    this.$('#qhosAccessToken').value = '';
    const epoch = this.epoch;
    this.connecting = true;
    this.$('#contractConnectionError').hidden = true;
    this.updateConnection();
    try {
      await this.connect({url, token});
      token = '';
      if (!this.active() || epoch !== this.epoch) return;
      this.notify?.('Coletor conectado. Confira os contratos disponíveis.');
      this.open({provider:'rithmic', preserveDraft:true});
    } catch (error) {
      if (!this.active() || epoch !== this.epoch) return;
      const message = token ? errorText(error).split(token).join('••••') : errorText(error);
      this.connectionError(message);
    } finally {
      token = '';
      if (this.active() && epoch === this.epoch) { this.connecting = false; this.updateConnection(); }
    }
  }

  updateConnection() {
    if (!this.active() || this.mode !== 'connection') return;
    const connection = this.state().connection ?? {}, available = availableConnection(connection), auto = connection.autoConnect ?? {};
    const busy = this.connecting || connection.state === 'connecting' || !!auto.attempting;
    const pending = busy || !!connection.retrying || !!auto.active && !available;
    const title = pending ? 'Conectando ao coletor' : available ? 'Coletor conectado' : 'Aguardando conexão';
    const detail = !available && auto.message ? String(auto.message) : pending ? 'Aguardando a confirmação do serviço QHOS.' : available ? connection.state === 'live' ? 'Recebendo dados da seleção confirmada.' : 'A conexão está confirmada. A chegada dos dados é acompanhada separadamente.' : 'Informe o endereço para consultar contratos e receber dados.';
    this.$('#contractConnectionStatus').innerHTML = `<span class="contract-connection-symbol">${icon(available ? 'check-circle' : pending ? 'clock' : 'layers')}</span><div><strong>${esc(title)}</strong><p>${esc(detail)}</p></div>${connection.environment === 'test' ? '<b class="contract-test-badge">TESTE</b>' : ''}`;
    this.$('#qhosConnectButton').disabled = busy;
    this.$('#qhosConnectButton').innerHTML = busy ? `${icon('clock')} Aguardando confirmação…` : `${icon('arrow-right')} ${available ? 'Reconectar coletor' : 'Conectar coletor'}`;
    this.$('#qhosEndpoint').disabled = busy;
    this.$('#qhosAccessToken').disabled = busy;
    this.$('#qhosDisconnectButton').hidden = !available && !pending;
    this.$('#qhosDisconnectButton').disabled = this.connecting;
    this.$('#qhosUseCoinbase').disabled = this.connecting;
  }
}
