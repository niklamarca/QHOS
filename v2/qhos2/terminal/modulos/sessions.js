// Local, bounded session archives. Market data never enters the paper account.
// IndexedDB writes resolve only after the transaction commits, not after put().
export const SESSION_FORMAT = 'qhos-session-v3';
export const SESSION_LIMITS = Object.freeze({ sessions: 50, trades: 20000, books: 600, depth: 20, candles: 500, bytes: 30 * 1024 * 1024 });

const DB_NAME = 'qhos-sessions';
const STORE_NAME = 'sessions';
const METADATA_NAME = 'session-metadata';
const SYMBOLS = new Set(['BTCUSD', 'ETHUSD', 'SOLUSD']);
const TIMEFRAMES = new Set(['1m', '5m', '15m', '1h', '4h', '1D']);
const KINDS = new Set(['auto', 'saved', 'imported']);
const SOURCE = 'Coinbase Exchange public market data';
const DAY = 86400000;
const bad = message => { throw new Error(message); };
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const positive = value => finite(value) && value > 0 && value <= 1e12;
const timestamp = value => finite(value) && value >= 946684800000 && value <= Date.now() + DAY;
const uuid = () => globalThis.crypto?.randomUUID?.() ?? `session-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const safeId = value => typeof value === 'string' && /^[A-Za-z0-9._:-]{1,100}$/.test(value);
const cleanName = value => typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 100) : '';
const byteSize = value => new TextEncoder().encode(typeof value === 'string' ? value : JSON.stringify(value)).byteLength;

// Instrument identity is part of the archive, independent of the live catalog.
// Old Coinbase archives retain their original ids and can still be replayed.
const LEGACY_NAMES = { BTCUSD: 'Bitcoin', ETHUSD: 'Ethereum', SOLUSD: 'Solana' };
function normalizeInstrument(value, symbol) {
  if (value == null && SYMBOLS.has(symbol)) return {
    id: symbol, symbol, name: LEGACY_NAMES[symbol], provider: 'coinbase', exchange: 'COINBASE', assetClass: 'spot',
    tickSize: .01, priceDecimals: 2, currency: 'USD', quantityUnit: symbol.replace(/USD$/, ''), contractMultiplier: 1,
    capabilities: { quotes: true, trades: true, depth: true, history: true },
  };
  if (!isObject(value)) bad('O contrato precisa de metadados de instrumento para ser salvo.');
  const provider = value.provider;
  if (!['coinbase', 'rithmic'].includes(provider)) bad('Fornecedor do instrumento inválido.');
  if (value.id !== symbol || typeof value.symbol !== 'string' || !/^[A-Za-z0-9._/-]{1,60}$/.test(value.symbol)) bad('Identidade do instrumento inconsistente.');
  if (typeof value.exchange !== 'string' || !/^[A-Za-z0-9._-]{1,32}$/.test(value.exchange)) bad('Bolsa do instrumento inválida.');
  if (provider === 'coinbase' && (!SYMBOLS.has(symbol) || value.exchange !== 'COINBASE' || value.symbol !== symbol)) bad('Instrumento Coinbase não reconhecido.');
  if (provider === 'rithmic' && symbol !== `rithmic:${value.exchange}:${value.symbol}`) bad('O ID do contrato deve identificar fornecedor, bolsa e símbolo.');
  if (!['spot', 'future', 'equity'].includes(value.assetClass) || provider === 'coinbase' && value.assetClass !== 'spot') bad('Classe de instrumento inválida.');
  if (!positive(value.tickSize) || value.tickSize < 1e-10 || !Number.isInteger(value.priceDecimals) || value.priceDecimals < 0 || value.priceDecimals > 10) bad('Tick ou precisão de preço inválidos.');
  if (Math.abs(Number(value.tickSize.toFixed(value.priceDecimals)) - value.tickSize) > Math.max(1e-12, value.tickSize * 1e-10)) bad('Precisão de preço insuficiente para representar o tick.');
  if (typeof value.currency !== 'string' || !/^[A-Z]{3}$/.test(value.currency)) bad('Moeda do instrumento inválida.');
  const quantityUnit = cleanName(value.quantityUnit), name = cleanName(value.name);
  if (!quantityUnit || quantityUnit.length > 40 || !name || !positive(value.contractMultiplier)) bad('Nome, unidade ou multiplicador do instrumento inválidos.');
  if (!isObject(value.capabilities) || !['quotes', 'trades', 'depth', 'history'].every(key => typeof value.capabilities[key] === 'boolean')) bad('Capacidades do instrumento inválidas.');
  let expiry;
  if (value.expiry != null || value.assetClass === 'future') {
    if (typeof value.expiry !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value.expiry) || !Number.isFinite(Date.parse(value.expiry)) || new Date(value.expiry).toISOString().slice(0, 10) !== value.expiry) bad('Vencimento do contrato inválido.');
    expiry = value.expiry;
  }
  return { id: symbol, symbol: value.symbol, name, provider, exchange: value.exchange, assetClass: value.assetClass,
    ...(expiry ? { expiry } : {}), tickSize: value.tickSize, priceDecimals: value.priceDecimals,
    currency: value.currency, quantityUnit, contractMultiplier: value.contractMultiplier,
    capabilities: Object.fromEntries(['quotes', 'trades', 'depth', 'history'].map(key => [key, value.capabilities[key]])) };
}

function eventMetadata(input, instrument, label, matchTime = false) {
  const result = { provider: instrument.provider, exchange: instrument.exchange };
  for (const key of ['provider', 'exchange']) if (input[key] != null && input[key] !== result[key]) bad(`${label}: origem diferente da sessão.`);
  for (const key of ['timeNs', 'timestampNs']) {
    if (input[key] == null) continue;
    if (typeof input[key] !== 'string' || !/^\d{15,22}$/.test(input[key])) bad(`${label}: ${key} deve ser uma string inteira em nanossegundos.`);
    const value = BigInt(input[key]);
    if (value < 946684800000000000n || value > BigInt(Date.now() + DAY) * 1000000n) bad(`${label}: ${key} fora do intervalo aceito.`);
    if (matchTime && value / 1000000n !== BigInt(Math.trunc(input.time))) bad(`${label}: timestamp em nanossegundos não corresponde ao horário em ms.`);
    result[key] = input[key];
  }
  if (result.timeNs && result.timestampNs && BigInt(result.timeNs) !== BigInt(result.timestampNs)) bad(`${label}: timestamps originais conflitantes.`);
  if (input.exchangeTime != null) {
    if (!timestamp(input.exchangeTime)) bad(`${label}: horário de bolsa inválido.`);
    result.exchangeTime = input.exchangeTime;
  }
  return result;
}

function dateValue(value, fallback) {
  const parsed = typeof value === 'string' ? Date.parse(value) : value;
  return timestamp(parsed) ? parsed : fallback;
}

function compareIds(a, b) {
  if (/^\d+$/.test(a) && /^\d+$/.test(b)) {
    const aa = a.replace(/^0+(?=\d)/, ''), bb = b.replace(/^0+(?=\d)/, '');
    return aa.length - bb.length || aa.localeCompare(bb);
  }
  return a.localeCompare(b);
}

// These fields are context only: replay does not execute imported settings or
// restore an account. Drop prototype keys and refuse oversized nested payloads.
function safeContext(value, label) {
  let nodes = 0;
  function copy(input, depth) {
    if (++nodes > 3000 || depth > 7) bad(`${label}: estrutura muito grande ou profunda.`);
    if (input === null || typeof input === 'boolean') return input;
    if (typeof input === 'number') return finite(input) ? input : bad(`${label}: número inválido.`);
    if (typeof input === 'string') return input.slice(0, 1000);
    if (Array.isArray(input)) {
      if (input.length > 1000) bad(`${label}: lista grande demais.`);
      return input.map(item => copy(item, depth + 1));
    }
    if (isObject(input)) {
      const result = {};
      const entries = Object.entries(input);
      if (entries.length > 100) bad(`${label}: campos em excesso.`);
      for (const [key, item] of entries) {
        if (['__proto__', 'prototype', 'constructor'].includes(key) || item === undefined) continue;
        if (key.length > 80) continue;
        result[key] = copy(item, depth + 1);
      }
      return result;
    }
    bad(`${label}: valor não compatível com JSON.`);
  }
  const result = copy(value ?? {}, 0);
  if (byteSize(result) > 200000) bad(`${label}: conteúdo grande demais.`);
  return result;
}

function normalizeTrades(input, symbol, instrument) {
  if (!Array.isArray(input)) bad('O arquivo precisa conter a lista de negócios (trades).');
  if (input.length > SESSION_LIMITS.trades) bad('A sessão excede o limite de 20.000 negócios.');
  const ids = new Set();
  const trades = input.map((trade, index) => {
    const label = `Negócio ${index + 1}`;
    if (!isObject(trade)) bad(`${label}: registro inválido.`);
    if (trade.symbol != null && trade.symbol !== symbol) bad(`${label}: ativo diferente da sessão.`);
    let id = typeof trade.id === 'number' && Number.isSafeInteger(trade.id) && trade.id >= 0 ? String(trade.id) : trade.id;
    if (!safeId(id)) bad(`${label}: identificador inválido.`);
    if (/^\d+$/.test(id)) id = id.replace(/^0+(?=\d)/, '');
    if (ids.has(id)) bad(`${label}: identificador repetido (${id}).`);
    ids.add(id);
    if (!timestamp(trade.time)) bad(`${label}: horário inválido; use milissegundos Unix.`);
    if (!positive(trade.price) || !positive(trade.size)) bad(`${label}: preço e quantidade precisam ser positivos e finitos.`);
    if (!['buy', 'sell'].includes(trade.side)) bad(`${label}: agressor precisa ser buy ou sell.`);
    if (trade.source != null && !['live', 'backfill'].includes(trade.source)) bad(`${label}: origem de captura inválida.`);
    return { symbol, id, time: trade.time, price: trade.price, size: trade.size, side: trade.side, source: trade.source === 'backfill' ? 'backfill' : 'live', ...eventMetadata(trade, instrument, label, true) };
  });
  trades.sort((a, b) => {
    if (a.time !== b.time) return a.time - b.time;
    const aa = a.timeNs ?? a.timestampNs, bb = b.timeNs ?? b.timestampNs;
    if (aa && bb && BigInt(aa) !== BigInt(bb)) return BigInt(aa) < BigInt(bb) ? -1 : 1;
    return compareIds(a.id, b.id);
  });
  return trades;
}

function normalizeBooks(input, symbol, instrument) {
  if (!Array.isArray(input)) bad('O arquivo precisa conter a lista de snapshots (books).');
  if (input.length > SESSION_LIMITS.books) bad('A sessão excede o limite de 600 snapshots do livro.');
  const books = input.map((book, index) => {
    const label = `Snapshot ${index + 1}`;
    if (!isObject(book) || !timestamp(book.time)) bad(`${label}: registro ou horário inválido.`);
    if (book.symbol != null && book.symbol !== symbol) bad(`${label}: ativo diferente da sessão.`);
    if (book.ready !== true) bad(`${label}: o livro precisa estar sincronizado.`);
    function levels(side) {
      const rows = book[side];
      if (!Array.isArray(rows) || rows.length < 1 || rows.length > SESSION_LIMITS.depth) bad(`${label}: cada lado deve conter de 1 a 20 níveis.`);
      const prices = new Set();
      const result = rows.map(row => {
        if (!Array.isArray(row) || row.length !== 2 || !positive(row[0]) || !positive(row[1])) bad(`${label}: nível com preço ou quantidade inválidos.`);
        if (prices.has(row[0])) bad(`${label}: preço duplicado no mesmo lado do livro.`);
        prices.add(row[0]);
        return [row[0], row[1]];
      });
      result.sort((a, b) => side === 'bids' ? b[0] - a[0] : a[0] - b[0]);
      return result;
    }
    const bids = levels('bids'), asks = levels('asks');
    if (bids[0][0] >= asks[0][0]) bad(`${label}: livro cruzado ou spread nulo.`);
    const result = { symbol, time: book.time, ready: true, bids, asks, ...eventMetadata(book, instrument, label) };
    if ((typeof book.sequence === 'string' && book.sequence.length <= 100) || (finite(book.sequence) && book.sequence >= 0)) result.sequence = book.sequence;
    return result;
  }).sort((a, b) => a.time - b.time);
  for (let i = 1; i < books.length; i++) {
    if (books[i].time - books[i - 1].time < 1000) bad('Snapshots precisam respeitar a amostragem de no máximo um por segundo.');
  }
  return books;
}

function normalizeCandles(input) {
  if (input == null) return [];
  if (!Array.isArray(input) || input.length > SESSION_LIMITS.candles) bad('O histórico deve conter no máximo 500 candles.');
  const times = new Set();
  return input.map((candle, index) => {
    const label = `Candle ${index + 1}`;
    if (!isObject(candle) || !finite(candle.time) || !timestamp(candle.time * 1000)) bad(`${label}: horário inválido; use segundos Unix.`);
    if (times.has(candle.time)) bad(`${label}: horário duplicado.`);
    times.add(candle.time);
    if (!['open', 'high', 'low', 'close'].every(key => positive(candle[key])) || !finite(candle.volume) || candle.volume < 0 || candle.volume > 1e15) bad(`${label}: OHLCV inválido.`);
    if (candle.high < Math.max(candle.open, candle.close, candle.low) || candle.low > Math.min(candle.open, candle.close, candle.high)) bad(`${label}: máxima e mínima inconsistentes.`);
    const result = { time: candle.time, open: candle.open, high: candle.high, low: candle.low, close: candle.close, volume: candle.volume };
    for (const flag of ['partial', 'captured']) if (typeof candle[flag] === 'boolean') result[flag] = candle[flag];
    return result;
  }).sort((a, b) => a.time - b.time);
}

function normalizeGaps(value) {
  if (value == null) return [];
  if (!Array.isArray(value) || value.length > 1000) bad('A lista de lacunas deve conter no máximo 1.000 registros.');
  return value.map((gap, index) => {
    if (!isObject(gap)) bad(`Lacuna ${index + 1}: registro inválido.`);
    const from = gap.from ?? gap.time;
    const to = gap.to ?? from;
    if (!timestamp(from) || !timestamp(to) || to < from) bad(`Lacuna ${index + 1}: intervalo inválido.`);
    const result = { from, to, reason: typeof gap.reason === 'string' ? gap.reason.slice(0, 200) : 'Interrupção registrada' };
    for (const key of ['missing', 'recovered', 'unrecovered', 'gaps']) if (finite(gap[key]) && gap[key] >= 0) result[key] = gap[key];
    return result;
  }).sort((a, b) => a.from - b.from);
}

function recordingAndSummary(trades, books, raw, symbol, createdAt, provenance) {
  const starts = [trades[0]?.time, books[0]?.time].filter(finite);
  const ends = [trades.at(-1)?.time, books.at(-1)?.time].filter(finite);
  const from = starts.length ? Math.min(...starts) : null;
  const to = ends.length ? Math.max(...ends) : null;
  const tradeTruncated = raw?.tradeTruncated === true;
  const bookTruncated = raw?.bookTruncated === true;
  const sessionStartedAt = timestamp(raw?.sessionStartedAt) ? raw.sessionStartedAt : from ?? createdAt;
  const recording = {
    symbol, from, to, trades: trades.length, frames: books.length, duration: from != null && to != null ? Math.max(0, to - from) : 0,
    truncated: tradeTruncated || bookTruncated || raw?.truncated === true, tradeTruncated, bookTruncated,
    retainedFrom: from, tradesFrom: trades[0]?.time ?? null, booksFrom: books[0]?.time ?? null, sessionStartedAt,
    maxTrades: SESSION_LIMITS.trades, maxBookFrames: SESSION_LIMITS.books, bookSampleIntervalMs: 1000,
    bookDepth: SESSION_LIMITS.depth, source: provenance.sourceLabel, provider: provenance.instrument.provider,
    exchange: provenance.instrument.exchange, environment: provenance.environment, timeUnit: 'milliseconds',
  };
  let buyVolume = 0, sellVolume = 0, notional = 0;
  for (const trade of trades) {
    if (trade.side === 'buy') buyVolume += trade.size; else sellVolume += trade.size;
    notional += trade.price * trade.size;
  }
  const totalVolume = buyVolume + sellVolume;
  const summary = {
    symbol, buyVolume, sellVolume, delta: buyVolume - sellVolume, totalVolume, notional,
    vwap: totalVolume ? notional / totalVolume : null, count: trades.length, from: trades[0]?.time ?? null,
    to: trades.at(-1)?.time ?? null, lastPrice: trades.at(-1)?.price ?? null,
    buyPercent: totalVolume ? buyVolume / totalVolume * 100 : null, truncated: tradeTruncated,
    retainedFrom: trades[0]?.time ?? null, sessionStartedAt, scope: 'retained_observed_trades',
  };
  return { recording, summary };
}

/** Return a normalized copy; throw a useful Portuguese error for invalid data. */
export function validateSession(value) {
  if (!isObject(value)) bad('Arquivo de sessão inválido: é necessário um objeto JSON.');
  const legacy = value.format === 'qhos-observed-flow-v1';
  if (!legacy && value.format !== SESSION_FORMAT) bad('Formato não reconhecido. Importe uma sessão QHOS v3 ou uma captura QHOS v2.');
  if (!legacy && value.schemaVersion != null && value.schemaVersion !== 3) bad('Versão de sessão não suportada.');
  const symbol = value.symbol;
  const instrument = normalizeInstrument(value.instrument, symbol);
  const environment = cleanName(value.environment).slice(0, 64);
  const sourceLabel = cleanName(value.sourceLabel) || (instrument.provider === 'coinbase' ? 'Coinbase Exchange' : `Rithmic${environment ? ` · ${environment}` : ''}`);
  const provenance = { instrument, sourceLabel, environment };
  const id = legacy && value.id == null ? uuid() : value.id;
  if (!safeId(id)) bad('Identificador da sessão inválido.');
  const timeframe = value.timeframe ?? '5m';
  if (!TIMEFRAMES.has(timeframe)) bad('Período do gráfico inválido.');
  const kind = value.kind ?? (legacy ? 'imported' : 'saved');
  if (!KINDS.has(kind)) bad('Tipo de sessão inválido.');
  const now = Date.now();
  const createdAt = dateValue(value.createdAt ?? value.exportedAt, now);
  const updatedAt = Math.max(createdAt, dateValue(value.updatedAt, createdAt));
  const trades = normalizeTrades(value.trades, symbol, instrument);
  const books = normalizeBooks(value.books, symbol, instrument);
  if (!trades.length && !books.length) bad('A sessão ainda não contém negócios ou snapshots para salvar.');
  const historyCandles = normalizeCandles(value.historyCandles);
  const { recording, summary } = recordingAndSummary(trades, books, value.recording, symbol, createdAt, provenance);
  const imported = value.imported === true || value.origin === 'imported-file' || kind === 'imported';
  const name = cleanName(value.name) || `${symbol} · ${new Date(recording.from ?? createdAt).toISOString().slice(0, 16).replace('T', ' ')} UTC`;
  const result = {
    format: SESSION_FORMAT, schemaVersion: 3, id, name, kind, createdAt, updatedAt,
    exportedAt: new Date(dateValue(value.exportedAt, updatedAt)).toISOString(), symbol, timeframe,
    source: instrument.provider === 'coinbase' ? SOURCE : sourceLabel, ...provenance,
    origin: imported ? 'imported-file' : 'browser-session', imported,
    sourceVerified: false, recording, summary, trades, books, historyCandles,
    settings: safeContext(value.settings ?? {}, 'Configurações'),
    diagnostics: safeContext(value.diagnostics ?? value.feedDiagnostics ?? {}, 'Diagnóstico'),
    gaps: normalizeGaps(value.gaps),
    method: {
      tradeSide: 'aggressor', tradeSizeUnit: instrument.quantityUnit, priceUnit: instrument.currency,
      timeUnit: 'milliseconds', originalTimestamp: 'Original nanosecond strings are retained when supplied; chart coordinates use milliseconds.',
      trades: 'Observed trades and marked REST backfills; exchange history may be incomplete.',
      book: 'L2 quantities aggregated by price; at most 20 bids and 20 asks sampled at most once per second.',
      replay: 'Only captured events and snapshots at or before the cursor; no interpolated liquidity.',
      limits: 'A bounded local archive, not a complete exchange recording. No MBO or individual queue positions.',
      importedData: 'Imported files are structurally validated but their exchange origin is not authenticated.',
    },
  };
  if (byteSize(result) > SESSION_LIMITS.bytes) bad('A sessão excede o limite de 30 MB.');
  return result;
}

/** Build a new checkpoint from flow.exportData(), with optional chart context. */
export function createSession(flowExport, context = {}) {
  if (!isObject(flowExport) || !isObject(context)) bad('Não foi possível preparar a sessão.');
  const now = Date.now();
  return validateSession({
    ...flowExport, ...context, format: SESSION_FORMAT, schemaVersion: 3,
    id: context.id ?? uuid(), name: context.name ?? flowExport.name,
    kind: context.kind ?? 'saved', createdAt: context.createdAt ?? now, updatedAt: now,
    exportedAt: new Date(now).toISOString(),
    timeframe: context.timeframe ?? flowExport.timeframe ?? '5m',
    historyCandles: context.historyCandles ?? flowExport.historyCandles ?? [],
    settings: context.settings ?? flowExport.settings ?? {},
    diagnostics: context.diagnostics ?? flowExport.diagnostics ?? flowExport.feedDiagnostics ?? {},
    gaps: context.gaps ?? flowExport.gaps ?? [],
  });
}

/** Parse data only. Importing never changes the account or replaces a recording. */
export function parseSession(text) {
  if (typeof text !== 'string') bad('O arquivo de sessão precisa ser JSON em texto.');
  if (text.length > SESSION_LIMITS.bytes || byteSize(text) > SESSION_LIMITS.bytes) bad('Arquivo muito grande: o limite é 30 MB.');
  let value;
  try { value = JSON.parse(text); } catch { bad('Não foi possível ler o JSON. Verifique se o arquivo foi exportado pelo QHOS.'); }
  if (!isObject(value)) bad('Arquivo de sessão inválido.');
  return validateSession({ ...value, origin: 'imported-file', imported: true });
}

function metadata(session) {
  return {
    id: session.id, name: session.name, kind: session.kind, symbol: session.symbol, timeframe: session.timeframe,
    createdAt: session.createdAt, updatedAt: session.updatedAt, imported: session.imported, origin: session.origin,
    instrument: session.instrument, source: session.source, sourceLabel: session.sourceLabel, environment: session.environment,
    recording: { ...session.recording }, sizeBytes: byteSize(session),
  };
}

function storageError(error) {
  if (error?.qhosStorageError) return error;
  const name = error?.name;
  let message = 'Não foi possível acessar as sessões salvas neste navegador.';
  if (name === 'QuotaExceededError') message = 'O armazenamento deste navegador está cheio. Exporte uma sessão e remova uma cópia local antes de salvar novamente.';
  if (name === 'SecurityError' || name === 'InvalidStateError') message = 'O armazenamento local está indisponível. Verifique as permissões do navegador; a exportação JSON continua disponível.';
  if (name === 'AbortError') message = 'A gravação da sessão foi interrompida. Tente salvar novamente.';
  const result = new Error(message);
  result.qhosStorageError = true;
  result.cause = error;
  return result;
}

function localError(message) {
  const error = new Error(message);
  error.qhosStorageError = true;
  return error;
}

let databasePromise = null;
function openDatabase() {
  if (databasePromise) return databasePromise;
  if (!globalThis.indexedDB) return Promise.reject(localError('Este navegador não oferece armazenamento de sessões. Você ainda pode exportar a captura em JSON.'));
  const pending = new Promise((resolve, reject) => {
    let request, settled = false;
    const fail = error => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      databasePromise = null;
      reject(storageError(error));
    };
    const timeout = setTimeout(() => fail(localError('O armazenamento demorou para responder. Feche outras abas do QHOS e tente novamente.')), 10000);
    try { request = indexedDB.open(DB_NAME, 2); } catch (error) { fail(error); return; }
    request.onupgradeneeded = () => {
      if (settled) { request.transaction?.abort(); return; }
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: 'id' });
        store.createIndex('updatedAt', 'updatedAt');
      }
      if (!db.objectStoreNames.contains(METADATA_NAME)) {
        const catalog = db.createObjectStore(METADATA_NAME, { keyPath: 'id' });
        catalog.createIndex('updatedAt', 'updatedAt');
        // Upgrade any earlier local prototype without removing its records.
        const cursorRequest = request.transaction.objectStore(STORE_NAME).openCursor();
        cursorRequest.onsuccess = () => {
          const cursor = cursorRequest.result;
          if (!cursor) return;
          try { catalog.put(metadata(cursor.value)); cursor.continue(); }
          catch { request.transaction.abort(); }
        };
      }
    };
    request.onerror = () => fail(request.error);
    request.onblocked = () => fail(localError('Outra aba está impedindo a atualização das sessões. Feche as outras abas do QHOS e tente novamente.'));
    request.onsuccess = () => {
      const db = request.result;
      if (settled) { db.close(); return; }
      settled = true;
      clearTimeout(timeout);
      db.onversionchange = () => { db.close(); databasePromise = null; };
      db.onclose = () => { databasePromise = null; };
      resolve(db);
    };
  });
  databasePromise = pending;
  // A synchronous open() exception can happen before the assignment above.
  // Clear that rejected cache too, so a later retry is not permanently blocked.
  pending.catch(() => { if (databasePromise === pending) databasePromise = null; });
  return pending;
}

async function transaction(mode, action, storeName = STORE_NAME) {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    let tx, result, failure;
    try { tx = db.transaction(mode === 'readwrite' ? [STORE_NAME, METADATA_NAME] : storeName, mode); }
    catch (error) { databasePromise = null; reject(storageError(error)); return; }
    const fail = error => {
      failure = error;
      try { tx.abort(); } catch { reject(storageError(error)); }
    };
    tx.oncomplete = () => resolve(result);
    tx.onabort = () => reject(storageError(failure ?? tx.error));
    tx.onerror = event => { failure ??= event.target?.error ?? tx.error; };
    try { action(tx.objectStore(storeName), value => { result = value; }, fail, mode === 'readwrite' ? tx.objectStore(METADATA_NAME) : null); }
    catch (error) { fail(error); }
  });
}

/** Newest first; data arrays stay out of the result returned to the UI. */
export async function listSessions() {
  return transaction('readonly', (store, done, fail) => {
    const records = [];
    const request = store.index('updatedAt').openCursor(null, 'prev');
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) { done(records); return; }
      try { records.push(cursor.value); } catch (error) { fail(error); return; }
      cursor.continue();
    };
  }, METADATA_NAME);
}

/** Atomic capacity check + write; does not silently evict any other session. */
export async function saveSession(value) {
  const session = validateSession(value);
  session.updatedAt = Date.now();
  return transaction('readwrite', (store, done, fail, catalog) => {
    const existing = store.get(session.id);
    const put = () => {
      const meta = metadata(session);
      const request = store.put(session);
      catalog.put(meta);
      request.onsuccess = () => done(meta);
    };
    existing.onsuccess = () => {
      if (existing.result) {
        session.createdAt = dateValue(existing.result.createdAt, session.createdAt);
        put();
      } else {
        const count = store.count();
        count.onsuccess = () => {
          if (count.result >= SESSION_LIMITS.sessions) { fail(localError('Você atingiu o limite de 50 sessões neste navegador. Exporte e remova uma sessão para liberar espaço.')); return; }
          put();
        };
      }
    };
  });
}

/** Return null if the id no longer exists. */
export async function getSession(id) {
  if (!safeId(id)) bad('Identificador da sessão inválido.');
  return transaction('readonly', (store, done, fail) => {
    const request = store.get(id);
    request.onsuccess = () => {
      try { done(request.result ? validateSession(request.result) : null); }
      catch { fail(localError('Esta sessão contém dados inválidos e não pode ser aberta.')); }
    };
  });
}

export async function deleteSession(id) {
  if (!safeId(id)) bad('Identificador da sessão inválido.');
  return transaction('readwrite', (store, done, fail, catalog) => {
    const request = store.delete(id);
    catalog.delete(id);
    request.onsuccess = () => done(true);
  });
}

export async function renameSession(id, name) {
  if (!safeId(id)) bad('Identificador da sessão inválido.');
  const normalizedName = cleanName(name);
  if (!normalizedName) bad('Informe um nome para a sessão.');
  return transaction('readwrite', (store, done, fail, catalog) => {
    const request = store.get(id);
    request.onsuccess = () => {
      if (!request.result) { fail(localError('A sessão não foi encontrada. Atualize a lista.')); return; }
      const session = { ...request.result, name: normalizedName, updatedAt: Date.now() };
      const meta = metadata(session);
      const write = store.put(session);
      catalog.put(meta);
      write.onsuccess = () => done(meta);
    };
  });
}
