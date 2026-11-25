import { buildExecutionEvents } from 'qhos-module/executions.js';
import { displayTime } from 'qhos-module/market.js';

const PREF_KEY = 'qhos-v2-execution-display-v1';
const $ = (selector, root = document) => root.querySelector(selector);
const price = (value, event = {}) => Number.isFinite(value) ? value.toLocaleString('pt-BR', Number.isInteger(event.priceDecimals) ? { minimumFractionDigits: event.priceDecimals, maximumFractionDigits: event.priceDecimals } : { minimumFractionDigits: 2, maximumFractionDigits: 8 }) : '—';
const quantity = value => Number.isFinite(value) ? value.toLocaleString('pt-BR', { maximumSignificantDigits: 12 }) : '—';
const number = value => value.toLocaleString('pt-BR');
const eventTime = value => displayTime.dateTime(value, { milliseconds: true });
const unit = value => value?.quantityUnit || value?.instrument?.quantityUnit || (['BTCUSD', 'ETHUSD', 'SOLUSD'].includes(value?.symbol) ? value.symbol.replace(/USD$/, '') : 'unidades');
const label = event => event.kind === 'paper' ? `${event.side === 'buy' ? 'Compra' : 'Venda'} · paper` : event.side === 'buy' ? 'Ask · compra agressora' : 'Bid · venda agressora';
const source = event => event.kind === 'paper' ? 'Simulador · relógio local' : event.source === 'imported' ? `Arquivo importado · origem não autenticada · ${event.sourceLabel}` : `${event.sourceLabel}${event.source === 'backfill' ? ' · recuperado' : ' · horário da bolsa'}`;
const originalTime = event => event.timeNs ?? event.timestampNs;
const node = (tag, className, text) => { const element = document.createElement(tag); if (className) element.className = className; if (text !== undefined) element.textContent = text; return element; };

function readPreferences() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(PREF_KEY)) || {}; } catch {}
  return { market: saved.market !== false, paper: saved.paper !== false,
    side: ['all', 'buy', 'sell'].includes(saved.side) ? saved.side : 'all',
    minSize: Number.isFinite(saved.minSize) && saved.minSize >= 0 && saved.minSize <= 1e12 ? saved.minSize : 0 };
}

export class ExecutionLayer {
  constructor(chart, { openModal, closeModal, notify, openChart, openHistory } = {}) {
    this.chart = chart;
    this.openModal = openModal;
    this.closeModal = closeModal;
    this.openChart = openChart;
    this.openHistory = openHistory;
    this.notify = notify || (() => {});
    this.prefs = readPreferences();
    this.context = null;
    this.events = [];
    this.byId = new Map();
    this.selectedIds = [];
    this.selectedIndex = 0;
    this.stage = $('.chart-stage');
    this.inspector = $('#executionInspector');
    this.tooltip = $('#executionTooltip');
    this.history = { events: [], page: 0, symbol: '', takenAt: Date.now(), initialized: false };
    this.historyTable = this.mountTable($('#executionHistoryTable'), this.history);
    $('#executionMarket').addEventListener('click', () => { this.prefs.market = !this.prefs.market; this.savePreferences(); });
    $('#executionPaper').addEventListener('click', () => { this.prefs.paper = !this.prefs.paper; this.savePreferences(); });
    $('#executionLatest').addEventListener('click', () => this.focus(this.events.at(-1)?.id));
    $('#executionList').addEventListener('click', () => this.openList());
    $('#executionSettings').addEventListener('click', () => this.openSettings());
    $('#executionHistoryLink').addEventListener('click', () => {
      this.refreshHistory();
      this.openHistory?.();
    });
    $('#executionHistoryRefresh').addEventListener('click', () => this.refreshHistory());
    $('#executionBackChart').addEventListener('click', () => this.scrollToChart());
    $('#executionInspectClose').addEventListener('click', () => this.clearSelection());
    $('#executionInspectZoom').addEventListener('click', () => this.zoomSelected(10));
    $('#executionInspectPrecision').addEventListener('click', () => this.zoomSelected(0.1));
    $('#executionInspectPrevious').addEventListener('click', () => this.stepSelection(-1));
    $('#executionInspectNext').addEventListener('click', () => this.stepSelection(1));
    this.inspector.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); this.clearSelection(); }
    });
  }

  scrollToChart() { this.openChart?.(); this.chart.resize(); }

  update(context) {
    const previous = this.context;
    this.context = context;
    this.events = buildExecutionEvents({ ...context, includeMarket: this.prefs.market,
      includePaper: this.prefs.paper && !context.replaying, side: this.prefs.side, minSize: this.prefs.minSize });
    this.byId = new Map(this.events.map(event => [event.id, event]));
    this.chart.setExecutions(this.events, { intervalSeconds: context.intervalSeconds, until: context.until ?? Infinity });
    const hadSelection = this.selectedIds.length > 0;
    const selectedId = this.selectedIds[this.selectedIndex];
    this.selectedIds = this.selectedIds.filter(id => this.byId.has(id));
    if (!this.selectedIds.length) { if (hadSelection) this.clearSelection(); }
    else {
      this.selectedIndex = Math.max(0, this.selectedIds.indexOf(selectedId));
      this.renderInspector();
    }
    $('#executionMarket').setAttribute('aria-pressed', String(this.prefs.market));
    $('#executionPaper').setAttribute('aria-pressed', String(this.prefs.paper && !context.replaying));
    $('#executionPaper').disabled = !!context.replaying;
    $('#executionPaper').title = context.replaying ? 'Operações da conta virtual ficam separadas do replay de mercado.' : 'Mostrar ou ocultar execuções da conta virtual';
    $('#executionCount').textContent = `${number(this.events.length)} registros`;
    $('#executionCount').title = 'Negócios retidos com os filtros atuais; o zoom pode agrupar marcas que ocupam os mesmos pixels.';
    $('#executionLatest').disabled = !this.events.length;
    const changedContext = previous && (previous.symbol !== context.symbol || !!previous.replaying !== !!context.replaying
      || !!previous.imported !== !!context.imported || context.replaying && context.until < previous.until);
    if (changedContext || !this.history.initialized && this.events.length) this.refreshHistory();
    else this.updateHistoryCount();
  }

  savePreferences() {
    try { localStorage.setItem(PREF_KEY, JSON.stringify(this.prefs)); }
    catch { this.notify('Não foi possível salvar os filtros neste navegador.', 'error'); }
    if (this.context) { this.update(this.context); this.refreshHistory(); }
  }

  hover(payload) {
    this.hoverPayload = payload;
    if (this.selectedIds.length || !payload?.events?.length) { this.tooltip.hidden = true; return; }
    const events = payload.events.filter(event => this.byId.has(event.id));
    if (!events.length) { this.tooltip.hidden = true; return; }
    this.tooltip.replaceChildren();
    if (events.length === 1) {
      const event = events[0];
      this.tooltip.append(node('strong', event.side, label(event)), node('span', 'execution-hover-price', `${price(event.price, event)} ${event.currency} · ${quantity(event.size)} ${unit(event)}`),
        node('time', '', eventTime(event.time)), node('small', '', event.averagePriceOnly ? 'Preço médio histórico · clique para inspecionar' : 'Clique para fixar e ampliar este instante'));
    } else {
      const first = events[0], last = events.at(-1), total = events.reduce((sum, event) => sum + event.size, 0);
      this.tooltip.append(node('strong', first.side, `${number(events.length)} execuções agrupadas`), node('span', '', `${quantity(total)} ${unit(first)} · ${label(first)}`),
        node('time', '', displayTime.range(first.time, last.time, { milliseconds: true })), node('small', '', 'Clique para ver cada negócio. O zoom separa as marcas.'));
    }
    this.tooltip.hidden = false;
    const width = this.stage.clientWidth, height = this.stage.clientHeight, boxW = Math.min(270, width - 20);
    this.tooltip.style.width = `${boxW}px`;
    const boxH = this.tooltip.offsetHeight;
    const x = Number.isFinite(payload.x) ? payload.x : 10, y = Number.isFinite(payload.y) ? payload.y : 90;
    this.tooltip.style.left = `${Math.max(8, Math.min(width - boxW - 8, x + 15))}px`;
    this.tooltip.style.top = `${Math.max(8, Math.min(height - boxH - 8, y + 15))}px`;
  }

  select(payload) {
    const seen = new Set();
    this.selectedIds = (payload?.events || []).filter(event => this.byId.has(event.id) && !seen.has(event.id) && seen.add(event.id))
      .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id)).map(event => event.id);
    this.selectedIndex = 0;
    this.tooltip.hidden = true;
    if (!this.selectedIds.length) { this.clearSelection(); return; }
    this.chart.selectExecution?.(this.selectedIds[0]);
    this.renderInspector();
  }

  clearSelection() {
    this.selectedIds = [];
    this.selectedIndex = 0;
    this.inspector.hidden = true;
    this.tooltip.hidden = true;
    this.chart.clearExecutionSelection?.();
  }

  renderInspector() {
    const event = this.byId.get(this.selectedIds[this.selectedIndex]);
    if (!event) { this.clearSelection(); return; }
    this.inspector.hidden = false;
    this.inspector.dataset.side = event.side;
    $('#executionInspectTitle').textContent = label(event);
    $('#executionInspectPrice').textContent = `${price(event.price, event)} ${event.currency}`;
    $('#executionInspectSize').textContent = `${quantity(event.size)} ${unit(event)}`;
    $('#executionInspectTime').textContent = eventTime(event.time);
    $('#executionInspectTime').title = originalTime(event) ? `Timestamp original: ${originalTime(event)} ns. O gráfico usa milissegundos.` : 'Timestamp disponível em milissegundos.';
    $('#executionInspectSource').textContent = source(event);
    $('#executionInspectId').textContent = event.tradeId ?? event.historyId ?? event.id;
    $('#executionInspectId').title = event.id;
    $('#executionInspectDetail').textContent = event.kind === 'paper'
      ? `${event.status}${event.averagePriceOnly ? ' · preço médio; níveis individuais não registrados' : ` · nível ${(event.fillIndex ?? 0) + 1}/${event.fillCount ?? 1}`}`
      : `Execução registrada · lado agressor.${originalTime(event) ? ` Original: ${originalTime(event)} ns; gráfico em ms.` : ' Liquidez cancelada do livro não gera esta marca.'}`;
    $('#executionInspectPosition').textContent = this.selectedIds.length > 1 ? `${this.selectedIndex + 1} de ${number(this.selectedIds.length)} no grupo` : 'Execução individual';
    $('#executionInspectPrevious').disabled = this.selectedIndex === 0;
    $('#executionInspectNext').disabled = this.selectedIndex >= this.selectedIds.length - 1;
    $('#executionInspectNavigation').hidden = this.selectedIds.length < 2;
  }

  stepSelection(direction) {
    this.selectedIndex = Math.max(0, Math.min(this.selectedIds.length - 1, this.selectedIndex + direction));
    this.chart.selectExecution?.(this.selectedIds[this.selectedIndex]);
    this.renderInspector();
  }

  zoomSelected(windowSeconds) {
    const id = this.selectedIds[this.selectedIndex];
    if (!id) return;
    if (!this.chart.focusExecution(id, { windowSeconds })) this.notify('O registro existe, mas seu candle não está no histórico disponível para este período.', 'error');
  }

  focus(id) {
    const event = this.byId.get(id);
    if (!event) { this.notify('Esta execução não está mais na captura ou nos filtros atuais. Atualize a lista.', 'error'); return false; }
    this.scrollToChart();
    const focused = this.chart.focusExecution(id, { windowSeconds: 10 });
    this.select({ events: [event] });
    if (!focused) this.notify('Registro selecionado. Seu candle está fora do histórico disponível neste período.', 'error');
    return focused;
  }

  openSettings() {
    if (!this.openModal) return;
    this.openModal('Execuções no gráfico.', `<form id="executionSettingsForm" class="modal-body-padding"><p class="modal-description">Cada marca representa um negócio executado. Compra agressora consome o ask; venda agressora consome o bid.</p><label class="check-setting"><span>Negócios do mercado <small>Ask em verde · bid em vermelho</small></span><input id="executionShowMarket" type="checkbox"></label><label class="check-setting"><span>Operações do simulador <small>Triângulos · ocultas durante o replay de mercado</small></span><input id="executionShowPaper" type="checkbox"></label><div class="form-row"><div class="form-field"><label for="executionSide">Lado da execução</label><select id="executionSide"><option value="all">Compras e vendas</option><option value="buy">Compras · ask no mercado</option><option value="sell">Vendas · bid no mercado</option></select></div><div class="form-field"><label for="executionMinimum" id="executionMinimumLabel">Tamanho mínimo</label><input id="executionMinimum" type="number" min="0" max="1000000000000" step="any" required></div></div><p class="settings-help">O filtro se aplica à quantidade de cada negócio ou nível executado. Marcas sobrepostas podem ser agrupadas nos mesmos pixels; aproxime o zoom ou clique para inspecionar todas. A janela de precisão cobre 100 ms, sem criar negócios entre as observações.</p><div class="form-error" id="executionSettingsError" hidden></div><button class="primary-button" type="submit">Aplicar ao gráfico</button></form>`, { name: 'execution-settings', eyebrow: 'NEGÓCIOS · PREÇO E TEMPO' });
    $('#executionShowMarket').checked = this.prefs.market;
    $('#executionShowPaper').checked = this.prefs.paper;
    $('#executionSide').value = this.prefs.side;
    $('#executionMinimum').value = this.prefs.minSize;
    $('#executionMinimumLabel').textContent = `Tamanho mínimo (${unit(this.context)})`;
    $('#executionSettingsForm').addEventListener('submit', event => {
      event.preventDefault();
      const raw = $('#executionMinimum').value, minimum = Number(raw);
      if (raw === '' || !Number.isFinite(minimum) || minimum < 0 || minimum > 1e12) {
        $('#executionSettingsError').textContent = 'Informe uma quantidade entre 0 e 1 trilhão, em unidades do ativo.';
        $('#executionSettingsError').hidden = false; return;
      }
      this.prefs = { market: $('#executionShowMarket').checked, paper: $('#executionShowPaper').checked, side: $('#executionSide').value, minSize: minimum };
      this.savePreferences(); this.closeModal?.();
    });
  }

  refreshHistory() {
    Object.assign(this.history, { events: this.events.slice().reverse(), page: 0, symbol: this.context?.instrument?.symbol || this.context?.symbol || '', takenAt: Date.now(), initialized: this.events.length > 0 });
    this.historyTable.render();
    this.updateHistoryCount();
    this.renderHistorySummary();
  }

  renderHistorySummary() {
    $('#executionHistorySummary').textContent = `${this.history.symbol || 'Ativo'} · ${number(this.history.events.length)} registros com os filtros atuais · lista atualizada ${eventTime(this.history.takenAt)}.`;
  }

  // Reformat existing records only: keep filters, pages, selection and the captured history intact.
  refreshTimeZone() {
    this.historyTable.render();
    this.renderHistorySummary();
    if (this.selectedIds.length) this.renderInspector();
    else if (!this.tooltip.hidden && this.hoverPayload) this.hover(this.hoverPayload);
    if (this.modalTableHost?.isConnected) {
      this.modalTable.render();
      $('#modalEyebrow').textContent = `NEGÓCIOS REGISTRADOS · ${displayTime.label()}`;
    }
  }

  updateHistoryCount() {
    const known = new Set(this.history.events.map(event => event.id));
    const fresh = this.events.reduce((count, event) => count + (known.has(event.id) ? 0 : 1), 0);
    $('#executionNewCount').textContent = fresh ? `${number(fresh)} novos registros` : 'Lista atualizada';
    $('#executionHistoryRefresh').classList.toggle('has-new', fresh > 0);
  }

  openList() {
    if (!this.openModal) return;
    const state = { events: this.events.slice().reverse(), page: 0, symbol: this.context?.instrument?.symbol || this.context?.symbol || '' };
    this.openModal('Cada execução, no seu instante.', '<div class="modal-body-padding execution-list-modal"><p class="modal-description">Lista capturada ao abrir, com os filtros do gráfico. Os negócios mais recentes aparecem primeiro. Use “Ver no gráfico” para inspecionar o preço e o horário.</p><div class="execution-list-summary"><span id="executionModalSummary"></span><button type="button" id="executionModalRefresh">Atualizar lista</button></div><div id="executionModalTable"></div></div>', { name: 'execution-list', wide: true, eyebrow: `NEGÓCIOS REGISTRADOS · ${displayTime.label()}` });
    const table = this.mountTable($('#executionModalTable'), state, true);
    this.modalTableHost = $('#executionModalTable'); this.modalTable = table;
    const render = () => { $('#executionModalSummary').textContent = `${state.symbol} · ${number(state.events.length)} execuções`; table.render(); };
    $('#executionModalRefresh').addEventListener('click', () => { state.events = this.events.slice().reverse(); state.page = 0; state.symbol = this.context?.instrument?.symbol || this.context?.symbol || ''; render(); });
    render();
  }

  mountTable(host, state, modal = false) {
    host.innerHTML = '<div class="execution-table-scroll" tabindex="0" aria-label="Tabela de execuções. Role horizontalmente para ver todas as colunas."><table class="execution-table"><thead><tr><th data-execution-zone>Horário</th><th>Execução</th><th>Preço</th><th>Quantidade</th><th>Origem</th><th>ID do registro</th><th><span class="execution-sr-only">Inspecionar</span></th></tr></thead><tbody></tbody></table></div><div class="execution-pagination"><span data-page-summary></span><div><button type="button" data-page-prev aria-label="Página anterior de execuções">← Anterior</button><button type="button" data-page-next aria-label="Próxima página de execuções">Próxima →</button></div></div>';
    const body = $('tbody', host), previous = $('[data-page-prev]', host), next = $('[data-page-next]', host), summary = $('[data-page-summary]', host);
    const render = () => {
      $('[data-execution-zone]', host).textContent = `Horário ${displayTime.label()}`;
      const pages = Math.max(1, Math.ceil(state.events.length / 30));
      state.page = Math.max(0, Math.min(pages - 1, state.page));
      const rows = document.createDocumentFragment();
      for (const event of state.events.slice(state.page * 30, (state.page + 1) * 30)) {
        const row = node('tr');
        const time = node('td', 'execution-table-time');
        time.append(node('time', '', displayTime.time(event.time, { milliseconds: true, zone: true })), node('small', '', displayTime.date(event.time, { year: true })));
        if (originalTime(event)) time.title = `Timestamp original: ${originalTime(event)} ns`;
        const name = node('td', event.side, `${event.kind === 'paper' ? '▲ ' : '● '}${label(event)}`);
        const value = node('td', 'execution-table-number', `${price(event.price, event)} ${event.currency}`);
        if (event.averagePriceOnly) value.append(node('small', '', 'Preço médio'));
        const size = node('td', 'execution-table-number', `${quantity(event.size)} ${unit(event)}`);
        const origin = node('td', 'execution-table-source', source(event));
        const identifier = node('td', 'execution-table-id', event.tradeId ?? event.historyId ?? event.id);
        identifier.title = event.id;
        const action = node('td'), button = node('button', 'execution-view-button', 'Ver no gráfico');
        button.type = 'button'; button.setAttribute('aria-label', `Ver ${label(event)} de ${eventTime(event.time)} no gráfico`);
        button.addEventListener('click', () => { if (modal) this.closeModal?.(); this.focus(event.id); });
        action.append(button); row.append(time, name, value, size, origin, identifier, action); rows.append(row);
      }
      if (!state.events.length) {
        const row = node('tr'), empty = node('td', 'execution-table-empty', 'Nenhuma execução nos filtros atuais. Aguarde negócios do feed ou ajuste os filtros.');
        empty.colSpan = 7; row.append(empty); rows.append(row);
      }
      body.replaceChildren(rows);
      previous.disabled = state.page === 0; next.disabled = state.page >= pages - 1;
      const first = state.events.length ? state.page * 30 + 1 : 0, last = Math.min(state.events.length, (state.page + 1) * 30);
      summary.textContent = `${number(first)}–${number(last)} de ${number(state.events.length)} · página ${state.page + 1}/${pages}`;
    };
    previous.addEventListener('click', () => { state.page--; render(); });
    next.addEventListener('click', () => { state.page++; render(); });
    render(); return { render };
  }
}
