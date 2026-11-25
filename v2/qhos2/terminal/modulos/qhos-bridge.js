/**
 * Client for the proposed QHOS dashboard bridge, NOT the Rithmic wire protocol.
 * The collector must implement docs/QHOS-BRIDGE-CONTRACT.md before this client
 * can connect. This module has no demo data, order API or credential storage.
 */
export const QHOS_BRIDGE_PROTOCOL = 'qhos.dashboard.v1';
const FRAMES = Object.freeze({ '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1D': 86400 });
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const positive = value => finite(value) && value > 0;
const decimal = value => typeof value === 'string' && /^(0|[1-9][0-9]{0,29})$/.test(value);
const fail = message => { throw new Error(message); };
const deepFreeze = value => { if (value && typeof value === 'object' && !Object.isFrozen(value)) { Object.values(value).forEach(deepFreeze); Object.freeze(value); } return value; };
// QHOS V2 task 9: the collector reports one state per layer of the path (pipeline message). Only these states exist;
// anything else is rejected, so a layer is never shown as healthy by default.
const PIPELINE_STATES = new Set(['ok', 'atencao', 'falha', 'desconhecido']);

function text(value, label, maximum = 160) {
  if (typeof value !== 'string' || !value || value !== value.trim() || value.length > maximum || /[\u0000-\u001f\u007f]/.test(value)) fail(`${label} inválido.`);
  return value;
}

function identifier(value, label, maximum = 160) {
  text(value, label, maximum);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:/-]*$/.test(value)) fail(`${label} inválido.`);
  return value;
}

function date(value, label) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) fail(`${label} inválido.`);
  const parsed = Date.parse(`${value}T00:00:00Z`);
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString().slice(0, 10) !== value) fail(`${label} inválido.`);
  return value;
}

/** Validate before rendering. Returned metadata contains no backend HTML/CSS. */
export function normalizeInstrument(raw) {
  if (!record(raw)) fail('Contrato inválido.');
  const exchange = identifier(raw.exchange, 'Bolsa', 32);
  const symbol = identifier(raw.symbol, 'Símbolo', 64);
  if (exchange.includes(':') || exchange.includes('/') || symbol.includes(':')) fail('Identidade do contrato inválida.');
  const id = `rithmic:${exchange}:${symbol}`;
  if (raw.id !== id || (raw.provider != null && raw.provider !== 'rithmic')) fail('O contrato precisa identificar fornecedor, bolsa e símbolo.');
  const name = text(raw.name, 'Nome do contrato', 160);
  if (typeof raw.currency !== 'string' || !/^[A-Z]{3}$/.test(raw.currency)) fail('Moeda do contrato inválida.');
  if (!['future', 'spot', 'equity'].includes(raw.assetClass)) fail('Classe do contrato inválida.');
  const expiry = raw.expiry == null ? null : date(raw.expiry, 'Vencimento');
  if (raw.assetClass === 'future' && !expiry) fail('O contrato futuro precisa de vencimento.');
  if (!positive(raw.tickSize) || raw.tickSize > 1e9 || !Number.isInteger(raw.priceDecimals) || raw.priceDecimals < 0 || raw.priceDecimals > 10) fail('Precisão de preço inválida.');
  const tickUnits = raw.tickSize * 10 ** raw.priceDecimals;
  if (Math.abs(tickUnits - Math.round(tickUnits)) > 1e-7 * Math.max(1, Math.abs(tickUnits)) || tickUnits < 1 - 1e-8) fail('O tick não corresponde à precisão do contrato.');
  if (!positive(raw.contractMultiplier) || raw.contractMultiplier > 1e12) fail('Multiplicador do contrato inválido.');
  const quantityUnit = text(raw.quantityUnit, 'Unidade de quantidade', 40);
  if (!record(raw.capabilities)) fail('Disponibilidade de dados ausente.');
  const capabilities = {};
  for (const key of ['quotes', 'trades', 'depth', 'history']) {
    if (typeof raw.capabilities[key] !== 'boolean') fail(`Disponibilidade de ${key} inválida.`);
    capabilities[key] = raw.capabilities[key];
  }
  return deepFreeze({ id, provider: 'rithmic', symbol, name, exchange, currency: raw.currency, assetClass: raw.assetClass,
    expiry, tickSize: raw.tickSize, priceDecimals: raw.priceDecimals, quantityUnit, contractMultiplier: raw.contractMultiplier, capabilities });
}

export function validateBridgeURL(value) {
  if (typeof value !== 'string' || value.length > 2048) fail('Informe o endereço seguro do serviço QHOS.');
  let url;
  try { url = new URL(value.trim()); } catch { fail('Endereço do serviço QHOS inválido.'); }
  // Plain ws:// only on IPv4 loopback with an explicit port: traffic never leaves the machine, and the
  // collector checks the token and the page origin. Any other host still requires wss://.
  const loopback = url.protocol === 'ws:' && url.hostname === '127.0.0.1' && url.port !== '';
  if ((url.protocol !== 'wss:' && !loopback) || !url.hostname || url.username || url.password || url.search || url.hash) fail('Use wss://, ou ws:// só em 127.0.0.1 com porta, sem credenciais, parâmetros ou fragmentos.');
  return url.href;
}

function instruments(raw, { maximum = 100, minimum = 0 } = {}) {
  if (!Array.isArray(raw) || raw.length < minimum || raw.length > maximum) fail('Lista de contratos inválida.');
  const result = raw.map(normalizeInstrument);
  if (new Set(result.map(item => item.id)).size !== result.length) fail('A lista contém contratos duplicados.');
  return result;
}

function normalizeSelection(raw) {
  if (!record(raw) || !decimal(raw.revision)) fail('Revisão da seleção inválida.');
  const list = instruments(raw.instruments, { maximum: 3, minimum: 1 });
  const subscriptionId = identifier(raw.subscriptionId, 'Identidade da assinatura');
  if (!list.some(item => item.id === raw.activeId)) fail('O contrato ativo não está na seleção confirmada.');
  return deepFreeze({ revision: raw.revision, subscriptionId, activeId: raw.activeId, instruments: list });
}

function messageError(raw, fallback) {
  // Server error text must not be reflected: it can contain credentials or URLs.
  const code = typeof raw?.code === 'string' ? raw.code : typeof raw === 'string' ? raw : '';
  const known = {
    UNAUTHORIZED: 'O serviço QHOS não autorizou a conexão. Confira a chave de acesso.',
    FORBIDDEN: 'O serviço QHOS não autorizou esta operação.',
    ENTITLEMENT_REQUIRED: 'A conexão não tem permissão para receber esse contrato.',
    INSTRUMENT_NOT_FOUND: 'O contrato não foi encontrado no coletor.',
    REVISION_CONFLICT: 'A seleção foi alterada em outro aplicativo. Atualize a conexão antes de tentar novamente.',
    SUBSCRIPTION_FAILED: 'O coletor não confirmou a assinatura do contrato.',
    HISTORY_UNAVAILABLE: 'O coletor não disponibilizou histórico para esse contrato.',
    HISTORY_OVERLAP: 'Capturas sobrepostas: não há recorte da captura selecionada disponível para exibição.',
    PROTOCOL_UNSUPPORTED: 'O serviço não é compatível com esta versão do dashboard.',
    RATE_LIMITED: 'O serviço pediu um intervalo antes da próxima consulta.'
  };
  const error = new Error(known[code] || fallback);
  error.code = own(known, code) ? code : 'BRIDGE_ERROR';
  return error;
}

/**
 * Options permit deterministic transport/clock tests; defaults use browser APIs.
 * All callbacks are synchronous. onSelection precedes status/data for that state.
 */
export function historyCoverageNotice(coverage) {
  return coverage?.kind === 'selected_capture_only'
    ? 'Histórico parcial: somente a captura selecionada; outras capturas se sobrepõem. Os candles não comprovam cobertura do período inteiro.' : '';
}

export function createQhosBridge(callbacks = {}, options = {}) {
  const Socket = options.WebSocketImpl ?? globalThis.WebSocket;
  const now = options.now ?? (() => Date.now());
  const later = options.setTimeout ?? ((fn, delay) => globalThis.setTimeout(fn, delay));
  const cancel = options.clearTimeout ?? (id => globalThis.clearTimeout(id));
  const requestTimeout = options.requestTimeoutMs ?? 12000;
  const handshakeTimeout = options.handshakeTimeoutMs ?? 12000;
  const staleAfter = options.staleAfterMs ?? 8000;
  const heartbeatTimeout = options.heartbeatTimeoutMs ?? 20000;
  const reconnectDelays = options.reconnectDelaysMs ?? [1000, 2000, 4000, 8000, 15000];
  let socket = null, generation = 0, sequence = 0, config = null, selection = null, sessionId = null;
  let welcome = null, connected = false, stopped = true, reconnectAttempt = 0, everConnected = false;
  let handshakeTimer = null, watchdogTimer = null, reconnectTimer = null, connectPending = null;
  let pendingSelection = null, lastActiveDataAt = null, lastActiveExchangeTime = null, pipeline = null;
  const requests = new Map(), tradeIds = new Set();
  let status = freshStatus();

  function freshStatus() {
    return { state: 'offline', provider: 'rithmic', environment: null, symbol: null, product: null, error: null,
      startedAt: null, lastMessageAt: null, lastHeartbeatAt: null, lastHeartbeatTime: null, lastTradeAt: null,
      lastTradeTime: null, lastTickerAt: null, lastBookAt: null, lastExchangeTime: null, reconnects: 0,
      gaps: null, missing: null, recovered: null, unrecovered: null, tradesReceived: 0, unclassifiedTrades: 0,
      invalidMessages: 0, ignoredMessages: 0, subscriptionConfirmed: false, selectionPending: false,
      bookReady: false, bookSequenceVerified: false, restRtt: null, recovering: false, retrying: false };
  }

  function invoke(name, value) {
    if (typeof callbacks[name] !== 'function') return;
    try { callbacks[name](value); } catch { /* Consumer failures cannot change subscription state. */ }
  }

  function emit(changes = {}) {
    status = { ...status, ...changes, selectionPending: pendingSelection !== null };
    invoke('onStatus', { ...status });
  }

  function snapshot() {
    return { ...status, connected, selection, pending: pendingSelection ? { ids: [...pendingSelection.ids], activeId: pendingSelection.activeId } : null,
      sessionId, capabilities: welcome ? [...welcome.capabilities] : [], pipeline };
  }

  function cleanupRequest(id) {
    const item = requests.get(id);
    if (!item) return null;
    requests.delete(id);
    cancel(item.timer);
    item.signal?.removeEventListener('abort', item.abort);
    if (pendingSelection?.id === id) pendingSelection = null;
    return item;
  }

  function rejectRequests(error) {
    for (const id of [...requests.keys()]) cleanupRequest(id)?.reject(error);
    pendingSelection = null;
  }

  function settleConnect(error, result) {
    const pending = connectPending;
    connectPending = null;
    if (pending) error ? pending.reject(error) : pending.resolve(result);
  }

  function closeSocket() {
    generation++;
    cancel(handshakeTimer); cancel(watchdogTimer);
    handshakeTimer = watchdogTimer = null;
    if (socket) {
      const old = socket; socket = null;
      old.onopen = old.onmessage = old.onerror = old.onclose = null;
      try { old.close(1000, 'QHOS reconnect'); } catch {}
    }
    connected = false;
    pipeline = null;
  }

  function terminalError(error) {
    cancel(reconnectTimer); reconnectTimer = null;
    stopped = true;
    closeSocket();
    rejectRequests(error);
    settleConnect(error);
    emit({ state: 'error', error: error.message, subscriptionConfirmed: false, bookReady: false, retrying: false });
  }

  function lostConnection(message = 'A conexão com o coletor foi interrompida.') {
    if (stopped) return;
    closeSocket();
    const error = new Error(message);
    rejectRequests(error); settleConnect(error);
    const retrying = everConnected && reconnectAttempt < reconnectDelays.length;
    if (retrying) {
      const delay = reconnectDelays[reconnectAttempt++];
      reconnectTimer = later(() => { reconnectTimer = null; if (!stopped && config) openSocket(true); }, delay);
    }
    // Consumers must see the scheduled retry in the same status notification.
    emit({ state: 'offline', error: message, subscriptionConfirmed: false, bookReady: false, retrying });
  }

  function send(message) {
    if (!socket || socket.readyState !== 1) fail('O serviço QHOS está desconectado.');
    // Authentication is sent only over this secure connection, never in a URL.
    socket.send(JSON.stringify(message));
  }

  function invalidateData() {
    lastActiveDataAt = lastActiveExchangeTime = null;
    tradeIds.clear();
    status = { ...status, lastTradeAt: null, lastTradeTime: null, lastTickerAt: null, lastBookAt: null,
      lastExchangeTime: null, bookReady: false, bookSequenceVerified: false, tradesReceived: 0, unclassifiedTrades: 0 };
  }

  function commitSelection(next, requestId = null, initial = false) {
    if (!initial && selection && BigInt(next.revision) <= BigInt(selection.revision)) return false;
    if (!initial && selection && next.subscriptionId === selection.subscriptionId) fail('O coletor reutilizou a identidade de uma assinatura anterior.');
    selection = next;
    invalidateData();
    const active = next.instruments.find(item => item.id === next.activeId);
    status = { ...status, symbol: active.id, product: active.symbol, state: 'waiting', error: null, retrying: false,
      subscriptionConfirmed: true, lastMessageAt: now() };
    const pending = pendingSelection;
    // A broadcast is authoritative even when another client requested it.
    let settled = null;
    if (pending) {
      const matches = next.activeId === pending.activeId && next.instruments.length === pending.ids.length
        && next.instruments.every(item => pending.ids.includes(item.id));
      const sameRequest = requestId === pending.id;
      if (matches && (sameRequest || requestId == null)) settled = { item: cleanupRequest(pending.id), value: next };
      else if (sameRequest || BigInt(next.revision) > BigInt(pending.expectedRevision)) settled = {
        item: cleanupRequest(pending.id), error: new Error('O coletor confirmou uma seleção diferente. A interface acompanha a seleção atual do serviço.')
      };
    }
    invoke('onSelection', next);
    emit();
    if (settled?.item) settled.error ? settled.item.reject(settled.error) : settled.item.resolve(settled.value);
    return true;
  }

  function startWatchdog(ownGeneration) {
    const tick = () => {
      if (ownGeneration !== generation || !connected || stopped) return;
      const time = now();
      if (status.lastMessageAt !== null && time - status.lastMessageAt > heartbeatTimeout) {
        lostConnection('O coletor deixou de responder. Aguardando reconexão.');
        return;
      }
      if (lastActiveDataAt !== null && (time - lastActiveDataAt > staleAfter || time - lastActiveExchangeTime > staleAfter)) {
        if (status.state !== 'stale') emit({ state: 'stale', bookReady: false });
      }
      watchdogTimer = later(tick, Math.min(1000, Math.max(20, staleAfter / 2)));
    };
    watchdogTimer = later(tick, Math.min(1000, Math.max(20, staleAfter / 2)));
  }

  function markData(time, changes = {}) {
    lastActiveDataAt = now(); lastActiveExchangeTime = time;
    const fresh = now() - time <= staleAfter && time <= now() + 1000;
    emit({ ...changes, state: fresh ? 'live' : 'stale', lastExchangeTime: time, error: null });
  }

  function eventContext(message) {
    if (!selection || !connected || message.subscriptionId !== selection.subscriptionId || message.revision !== selection.revision) return null;
    return selection.instruments.find(item => item.id === message.instrumentId) ?? null;
  }

  function eventTime(message) {
    if (!Number.isSafeInteger(message.time) || message.time <= 0 || message.time > 8640000000000000 || message.time > now() + 30000) fail('Horário do evento inválido.');
    let timeNs;
    if (message.timeNs != null) {
      if (!decimal(message.timeNs) || BigInt(message.timeNs) / 1000000n !== BigInt(message.time)) fail('Precisão do horário inconsistente.');
      timeNs = message.timeNs;
    }
    return { time: message.time, ...(timeNs ? { timeNs } : {}), receivedAt: now() };
  }

  function ticker(message, instrument) {
    if (!instrument.capabilities.quotes) return;
    const result = { symbol: instrument.id, product: instrument.symbol, provider: 'rithmic', exchange: instrument.exchange, source: 'live', ...eventTime(message) };
    let quote = false;
    for (const key of ['price', 'bid', 'ask', 'open', 'high', 'low', 'volume']) {
      if (!own(message, key)) continue;
      if (message[key] !== null && (!finite(message[key]) || (key === 'volume' ? message[key] < 0 : message[key] <= 0))) fail('Cotação inválida.');
      result[key] = message[key];
      if (['price', 'bid', 'ask'].includes(key) && message[key] !== null) quote = true;
    }
    if (!quote) fail('Cotação sem preço observado.');
    if (result.bid != null && result.ask != null && result.bid > result.ask) fail('Cotação cruzada inválida.');
    invoke('onTicker', result);
    if (instrument.id === selection.activeId) markData(result.time, { lastTickerAt: now() });
  }

  function trade(message, instrument) {
    if (instrument.id !== selection.activeId || !instrument.capabilities.trades) return;
    const id = typeof message.id === 'number' && Number.isSafeInteger(message.id) && message.id >= 0 ? String(message.id) : identifier(message.id, 'ID do negócio', 160);
    if (!positive(message.price) || !positive(message.size)) fail('Preço ou quantidade do negócio inválido.');
    const timing = eventTime(message);
    if (tradeIds.has(id)) return;
    if (![undefined, null, 'unknown', 'buy', 'sell'].includes(message.side)) fail('Lado agressor inválido.');
    tradeIds.add(id);
    if (tradeIds.size > 5000) tradeIds.delete(tradeIds.values().next().value);
    if (!['buy', 'sell'].includes(message.side)) {
      // Unknown is never guessed from the quote or treated as a sell.
      invoke('onTicker', { symbol: instrument.id, product: instrument.symbol, provider: 'rithmic', exchange: instrument.exchange, source: 'live', price: message.price, ...timing });
      markData(timing.time, { unclassifiedTrades: status.unclassifiedTrades + 1, lastTradeAt: now(), lastTradeTime: timing.time });
      return;
    }
    invoke('onTrade', { symbol: instrument.id, product: instrument.symbol, provider: 'rithmic', exchange: instrument.exchange,
      source: 'live', id, price: message.price, size: message.size, side: message.side, ...timing });
    markData(timing.time, { tradesReceived: status.tradesReceived + 1, lastTradeAt: now(), lastTradeTime: timing.time });
  }

  function book(message, instrument) {
    if (instrument.id !== selection.activeId || !instrument.capabilities.depth) return;
    if (typeof message.ready !== 'boolean') fail('Estado do livro inválido.');
    const timing = eventTime(message);
    if (!message.ready) {
      invoke('onBook', { symbol: instrument.id, product: instrument.symbol, provider: 'rithmic', exchange: instrument.exchange, bids: [], asks: [], ready: false, time: now(), exchangeTime: timing.time, receivedAt: now(), sequenceVerified: false });
      emit({ bookReady: false, bookSequenceVerified: false });
      return;
    }
    const levels = (rows, descending) => {
      if (!Array.isArray(rows) || !rows.length || rows.length > 2000) fail('Snapshot do livro inválido.');
      const result = rows.map(row => {
        if (!Array.isArray(row) || row.length !== 2 || !positive(row[0]) || !positive(row[1])) fail('Nível do livro inválido.');
        return [row[0], row[1]];
      }).sort((a, b) => descending ? b[0] - a[0] : a[0] - b[0]);
      if (new Set(result.map(row => row[0])).size !== result.length) fail('Snapshot com níveis duplicados.');
      return result.slice(0, 40);
    };
    const bids = levels(message.bids, true), asks = levels(message.asks, false);
    if (bids[0][0] > asks[0][0]) fail('Snapshot do livro cruzado.');
    const sequenceVerified = message.sequenceVerified === true && decimal(message.sequence);
    const fresh = now() - timing.time <= staleAfter && timing.time <= now() + 1000;
    invoke('onBook', { symbol: instrument.id, product: instrument.symbol, provider: 'rithmic', exchange: instrument.exchange,
      bids, asks, ready: fresh, time: now(), receivedAt: now(), exchangeTime: timing.time, ...(timing.timeNs ? { timeNs: timing.timeNs } : {}),
      sequenceVerified, ...(decimal(message.sequence) ? { sequence: message.sequence } : {}) });
    markData(timing.time, { lastBookAt: now(), bookReady: fresh, bookSequenceVerified: sequenceVerified });
  }

  function receiveResponse(message) {
    const pending = requests.get(message.id);
    if (!pending) return;
    if (typeof message.ok !== 'boolean') fail('Resposta do coletor inválida.');
    if (!message.ok) {
      cleanupRequest(message.id)?.reject(messageError(message.error, 'O coletor recusou a solicitação.'));
      emit(); return;
    }
    if (pending.method === 'selection.set') {
      // Acceptance is not proof of subscription, metadata or data arrival.
      if (!record(message.result) || message.result.status !== 'accepted') {
        cleanupRequest(message.id)?.reject(new Error('O coletor não confirmou o recebimento da seleção.'));
        emit();
      }
      return;
    }
    const request = cleanupRequest(message.id);
    try { request.resolve(request.transform(message.result)); }
    catch (error) { request.reject(error); }
  }

  function normalizePipeline(message) {
    if (!welcome.capabilities.includes('pipeline')) fail('O coletor enviou o percurso sem anunciar a capacidade.');
    if (message.sessionId !== sessionId || !Number.isSafeInteger(message.time) || message.time < 0
      || !Array.isArray(message.stages) || !message.stages.length || message.stages.length > 12) fail('Percurso do coletor inválido.');
    const ids = new Set();
    const stages = message.stages.map(row => {
      if (!record(row) || typeof row.id !== 'string' || !/^[a-z]{1,20}$/.test(row.id) || ids.has(row.id) || !PIPELINE_STATES.has(row.state)
        || !Number.isSafeInteger(row.since) || row.since < 0 || row.since > message.time || typeof row.reason !== 'string'
        || row.reason.length > 160 || !Array.isArray(row.fields) || row.fields.length > 12) fail('Percurso do coletor inválido.');
      ids.add(row.id);
      const fields = row.fields.map(field => {
        if (!record(field) || typeof field.label !== 'string' || field.label.length > 40 || typeof field.value !== 'string' || field.value.length > 80) fail('Percurso do coletor inválido.');
        return { label: field.label, value: field.value };
      });
      return { id: row.id, state: row.state, since: row.since, reason: row.reason, fields };
    });
    return deepFreeze({ time: message.time, receivedAt: now(), stages });
  }

  function receive(raw, ownGeneration) {
    if (ownGeneration !== generation || stopped) return;
    try {
      if (typeof raw !== 'string' || raw.length > 2000000) fail('Mensagem do coletor inválida.');
      const message = JSON.parse(raw);
      if (!record(message) || typeof message.type !== 'string') fail('Mensagem do coletor inválida.');
      status.lastMessageAt = now();
      if (message.type === 'error') {
        const error = messageError(message, 'O coletor informou uma falha na conexão.');
        terminalError(error); return;
      }
      if (!connected) {
        if (message.type !== 'welcome') fail('O serviço não confirmou o protocolo do dashboard.');
        if (message.protocol !== QHOS_BRIDGE_PROTOCOL || message.provider !== 'rithmic' || !['test', 'paper', 'synthetic', 'unknown', 'production'].includes(message.environment)) fail('O serviço não é compatível com o protocolo QHOS esperado.');
        const nextSession = identifier(message.sessionId, 'Sessão do serviço');
        if (!Array.isArray(message.capabilities) || message.capabilities.length > 16 || !message.capabilities.every(item => typeof item === 'string' && /^[a-z][a-z.]{0,40}$/.test(item)) || !['catalog', 'selection'].every(item => message.capabilities.includes(item))) fail('O serviço não permite pesquisar e selecionar contratos.');
        const next = normalizeSelection(message.selection);
        cancel(handshakeTimer); handshakeTimer = null;
        sessionId = nextSession;
        welcome = deepFreeze({ protocol: QHOS_BRIDGE_PROTOCOL, provider: 'rithmic', environment: message.environment, sessionId,
          capabilities: [...new Set(message.capabilities)], selection: next });
        connected = everConnected = true; reconnectAttempt = 0;
        status.environment = message.environment;
        commitSelection(next, null, true);
        settleConnect(null, welcome);
        startWatchdog(ownGeneration);
        return;
      }
      if (message.type === 'welcome') fail('O serviço enviou uma segunda confirmação na mesma conexão.');
      if (message.type === 'heartbeat') {
        emit({ lastHeartbeatAt: now(), lastHeartbeatTime: Number.isSafeInteger(message.time) ? message.time : null });
        return;
      }
      if (message.type === 'response') { receiveResponse(message); return; }
      if (message.type === 'selection') { commitSelection(normalizeSelection(message.selection), message.requestId); return; }
      if (message.type === 'pipeline') { pipeline = null; pipeline = normalizePipeline(message); invoke('onPipeline', pipeline); return; }
      if (!['ticker', 'trade', 'book', 'gap'].includes(message.type)) return;
      const instrument = eventContext(message);
      if (!instrument) { status.ignoredMessages++; return; }
      if (message.type === 'ticker') ticker(message, instrument);
      if (message.type === 'trade') trade(message, instrument);
      if (message.type === 'book') book(message, instrument);
      if (message.type === 'gap' && instrument.id === selection.activeId) {
        const missing = Number.isSafeInteger(message.missing) && message.missing >= 0 ? message.missing : null;
        // Count reports, not inferred sequence holes. Without an audited recovery
        // protocol, aggregate missing/recovered/unrecovered remain unknown.
        emit({ gaps: (status.gaps ?? 0) + 1, bookReady: false, state: 'stale' });
        invoke('onGap', { symbol: instrument.id, time: now(), type: 'reported', reason: 'collector', missing, gaps: status.gaps });
      }
    } catch (error) {
      if (!connected) terminalError(new Error('O serviço não confirmou um protocolo e uma seleção válidos.'));
      else {
        // Invalid selection must never partly update a working screen.
        status.invalidMessages++;
        emit({ error: error instanceof SyntaxError ? 'O coletor enviou uma mensagem inválida.' : error.message });
      }
    }
  }

  function openSocket(reconnecting = false) {
    if (!config || stopped) return;
    closeSocket();
    const ownGeneration = generation;
    invalidateData();
    emit({ state: 'connecting', error: null, subscriptionConfirmed: false, bookReady: false, retrying: true,
      reconnects: status.reconnects + (reconnecting ? 1 : 0), lastMessageAt: null });
    try { socket = new Socket(config.url); }
    catch { lostConnection('Não foi possível abrir uma conexão segura com o serviço QHOS.'); return; }
    socket.onopen = () => {
      if (ownGeneration !== generation || stopped) return;
      try { send({ type: 'hello', protocol: QHOS_BRIDGE_PROTOCOL, ...(config.token ? { token: config.token } : {}) }); }
      catch { lostConnection('Não foi possível iniciar a sessão QHOS.'); }
    };
    socket.onmessage = event => receive(event.data, ownGeneration);
    socket.onerror = () => {
      if (ownGeneration === generation && !stopped) lostConnection('Não foi possível manter a conexão segura com o serviço QHOS.');
    };
    socket.onclose = () => {
      if (ownGeneration === generation && !stopped) lostConnection();
    };
    handshakeTimer = later(() => {
      if (ownGeneration === generation && !connected && !stopped) lostConnection('O serviço QHOS não confirmou a conexão dentro do prazo.');
    }, handshakeTimeout);
  }

  function disconnect() {
    stopped = true;
    cancel(reconnectTimer); reconnectTimer = null;
    closeSocket();
    const error = new Error('Conexão QHOS encerrada.');
    rejectRequests(error); settleConnect(error);
    config = null; welcome = null; sessionId = null;
    lastActiveDataAt = lastActiveExchangeTime = null;
    emit({ state: 'offline', subscriptionConfirmed: false, bookReady: false, error: null, retrying: false });
  }

  function connect({ url, token = '' } = {}) {
    let normalized;
    try {
      normalized = validateBridgeURL(url);
      if (typeof Socket !== 'function') fail('Este navegador não oferece uma conexão WebSocket.');
      if (typeof token !== 'string' || token.length > 8192 || /[\u0000-\u001f\u007f]/.test(token)) fail('Chave de acesso inválida.');
    } catch (error) { return Promise.reject(error); }
    disconnect();
    config = { url: normalized, token };
    selection = null; status = freshStatus(); status.startedAt = now();
    stopped = false; everConnected = false; reconnectAttempt = 0;
    return new Promise((resolve, reject) => { connectPending = { resolve, reject }; openSocket(); });
  }

  function reconnect() {
    if (!config) return Promise.reject(new Error('Configure a conexão QHOS antes de reconectar.'));
    const remembered = { ...config };
    return connect(remembered);
  }

  function request(method, params, { signal, transform = value => value } = {}) {
    if (!connected || !socket || socket.readyState !== 1) return Promise.reject(new Error('Conecte o serviço QHOS para continuar.'));
    if (signal?.aborted) return Promise.reject(signal.reason ?? new DOMException('Operação cancelada.', 'AbortError'));
    const id = `qhos-${++sequence}`;
    return new Promise((resolve, reject) => {
      const abort = () => { cleanupRequest(id)?.reject(signal.reason ?? new DOMException('Operação cancelada.', 'AbortError')); emit(); };
      const timer = later(() => { cleanupRequest(id)?.reject(new Error(method === 'selection.set' ? 'A assinatura não foi confirmada dentro do prazo. A seleção anterior permanece até uma confirmação do coletor.' : 'O serviço QHOS não respondeu dentro do prazo.')); emit(); }, requestTimeout);
      requests.set(id, { id, method, params, resolve, reject, timer, signal, abort, transform });
      signal?.addEventListener('abort', abort, { once: true });
      if (method === 'selection.set') pendingSelection = { id, ...params };
      try { send({ type: 'request', id, method, params }); emit(); }
      catch { cleanupRequest(id)?.reject(new Error('Não foi possível enviar a solicitação ao coletor.')); emit(); }
    });
  }

  function search(query, { signal } = {}) {
    if (typeof query !== 'string' || query.length > 120 || /[\u0000-\u001f\u007f]/.test(query)) return Promise.reject(new Error('Busca de contratos inválida.'));
    return request('catalog.search', { query: query.trim(), limit: 100 }, { signal, transform: result => {
      if (!record(result)) fail('Resposta de catálogo inválida.');
      return instruments(result.instruments);
    } });
  }

  function setSelection({ ids, activeId } = {}) {
    if (!connected) return Promise.reject(new Error('Conecte o serviço QHOS para selecionar contratos.'));
    if (pendingSelection) return Promise.reject(new Error('Aguarde a confirmação da seleção em andamento.'));
    if (!selection || !Array.isArray(ids) || !ids.length || ids.length > 3 || new Set(ids).size !== ids.length || !ids.includes(activeId)
      || !ids.every(id => typeof id === 'string' && /^rithmic:[A-Za-z0-9][A-Za-z0-9_.-]{0,31}:[A-Za-z0-9][A-Za-z0-9_./-]{0,63}$/.test(id))) return Promise.reject(new Error('Seleção de contratos inválida.'));
    if (selection.activeId === activeId && ids.length === selection.instruments.length && selection.instruments.every(item => ids.includes(item.id))) return Promise.resolve(selection);
    return request('selection.set', { ids: [...ids], activeId, expectedRevision: selection.revision });
  }

  function loadCandles(instrumentId, timeframe, { signal } = {}) {
    const instrument = selection?.instruments.find(item => item.id === instrumentId);
    if (!instrument || !instrument.capabilities.history || !welcome?.capabilities.includes('history')) return Promise.reject(new Error('Histórico não disponibilizado pelo coletor para este contrato.'));
    if (!own(FRAMES, timeframe)) return Promise.reject(new Error('Período de candles inválido.'));
    const subscriptionId = selection.subscriptionId;
    return request('history.candles', { instrumentId, timeframe, limit: 240 }, { signal, transform: result => {
      if (selection?.subscriptionId !== subscriptionId || result?.instrumentId !== instrumentId || result?.timeframe !== timeframe || !Array.isArray(result.candles) || result.candles.length > 10000) fail('O histórico não corresponde ao contrato e período solicitados.');
      let coverage = null;
      if (result.coverage != null) {
        const c = result.coverage;
        if (!record(c) || c.kind !== 'selected_capture_only' || c.reason !== 'overlapping_captures'
          || c.partial !== true || c.periodComplete !== false || typeof c.captureId !== 'string'
          || !/^[A-Za-z0-9_-]{1,160}$/.test(c.captureId) || !result.candles.length) fail('Cobertura do histórico inválida.');
        coverage = deepFreeze({kind: c.kind, reason: c.reason, partial: true, periodComplete: false, captureId: c.captureId});
      }
      let previous = -1;
      const candles = result.candles.map(row => {
        if (!record(row) || !Number.isSafeInteger(row.time) || row.time <= previous || row.time < 0 || row.time > now() / 1000 || row.time % FRAMES[timeframe] !== 0
          || ![row.open, row.high, row.low, row.close].every(positive) || !finite(row.volume) || row.volume < 0
          || row.high < Math.max(row.open, row.close, row.low) || row.low > Math.min(row.open, row.close)
          || (row.partial != null && typeof row.partial !== 'boolean') || (coverage && row.partial !== true)) fail('O coletor retornou candles inválidos.');
        previous = row.time;
        return { time: row.time, open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume,
          partial: row.partial === true || (row.time + FRAMES[timeframe]) * 1000 > now(), source: 'rithmic' };
      });
      Object.defineProperty(candles, 'coverage', {value: coverage});
      return candles;
    } });
  }

  return Object.freeze({ connect, reconnect, disconnect, search, setSelection, loadCandles, getSnapshot: snapshot });
}
