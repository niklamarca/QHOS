// Executions are anchored to recorded millisecond timestamps and prices.
// A change in book liquidity alone is never an execution event.
const finite = value => typeof value === 'number' && Number.isFinite(value);
const positive = value => finite(value) && value > 0;
const cleanText = (value, limit = 160) => typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, limit) : '';
const normalizedStatus = value => cleanText(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const safeId = value => (typeof value === 'string' && value.length > 0 && value.length <= 160)
  || (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0);
const originalNs = value => {
  const stamp = value.timeNs ?? value.timestampNs;
  return typeof stamp === 'string' && /^\d{15,22}$/.test(stamp) ? stamp : null;
};
const chronology = (a, b) => {
  if (a.time !== b.time) return a.time - b.time;
  const aa = originalNs(a), bb = originalNs(b);
  if (aa && bb && BigInt(aa) !== BigInt(bb)) return BigInt(aa) < BigInt(bb) ? -1 : 1;
  return a.id.localeCompare(b.id);
};

function isExecutionStatus(value) {
  const status = normalizedStatus(value);
  if (/^(?:cancel|rejeit|reject|pendente|pending|abert|open|aguard|working|falh|failed)/.test(status)) return false;
  return /(?:executad|executed|preenchid|filled|parcial|partial|encerrad|fechad|closed)/.test(status);
}

function paperRole(row) {
  if (row.role === 'entry' || row.role === 'exit') return row.role;
  return /^(?:saida|stop|alvo|encerrad|fechad|closed|exit)/.test(normalizedStatus(row.status)) ? 'exit' : 'entry';
}

/**
 * Build a bounded, fresh event list without mutating the capture or account.
 * `time` and `until` use Unix milliseconds. The normalized trade side is the
 * AGGRESSOR: buy consumes ask liquidity; sell consumes bid liquidity.
 * Paper events are simulation records, so they do not claim exchange liquidity.
 * The minimum size applies to each displayed execution/price-level fill.
 */
export function buildExecutionEvents({
  trades = [], paperHistory = [], symbol, until = Infinity,
  includeMarket = true, includePaper = true, side = 'all', minSize = 0, imported = false,
  instrument = null, sourceLabel = '', environment = ''
} = {}) {
  if (typeof symbol !== 'string' || !symbol || !(finite(until) || until === Infinity)) return [];
  const floor = finite(minSize) && minSize > 0 ? minSize : 0;
  const selectedSide = side === 'ask' ? 'buy' : side === 'bid' ? 'sell' : side;
  const acceptsSide = value => (value === 'buy' || value === 'sell') && (selectedSide === 'all' || selectedSide === value);
  const events = [];
  const legacy = ['BTCUSD', 'ETHUSD', 'SOLUSD'].includes(symbol);
  const metadata = { displaySymbol: cleanText(instrument?.symbol || symbol),
    provider: instrument?.provider ?? (legacy ? 'coinbase' : null), exchange: cleanText(instrument?.exchange),
    sourceLabel: cleanText(sourceLabel) || (legacy ? 'Coinbase Exchange' : 'Fonte não informada'), environment: cleanText(environment),
    quantityUnit: cleanText(instrument?.quantityUnit) || (legacy ? symbol.replace(/USD$/, '') : 'unidades'),
    currency: cleanText(instrument?.currency) || (legacy ? 'USD' : ''),
    priceDecimals: Number.isInteger(instrument?.priceDecimals) && instrument.priceDecimals >= 0 && instrument.priceDecimals <= 10 ? instrument.priceDecimals : null };

  if (includeMarket && Array.isArray(trades)) {
    const seen = new Set(), market = [];
    for (const trade of trades) {
      if (!trade || trade.symbol !== symbol || !safeId(trade.id) || !positive(trade.time)
        || trade.time > until || !positive(trade.price) || !positive(trade.size) || !acceptsSide(trade.side)) continue;
      const id = `market:${symbol}:${String(trade.id)}`;
      if (seen.has(id)) continue;
      seen.add(id);
      const timeMetadata = {};
      for (const key of ['timeNs', 'timestampNs']) if (typeof trade[key] === 'string' && /^\d{15,22}$/.test(trade[key])) timeMetadata[key] = trade[key];
      market.push({ id, symbol, time: trade.time, price: trade.price, size: trade.size, ...metadata, ...timeMetadata,
        side: trade.side, kind: 'market', source: imported ? 'imported' : trade.source === 'backfill' ? 'backfill' : 'live',
        liquidity: trade.side === 'buy' ? 'ask' : 'bid',
        status: trade.side === 'buy' ? 'Compra no ask' : 'Venda no bid', tradeId: String(trade.id) });
    }
    // Input order may contain recovery records, so retain by event time.
    events.push(...market.sort(chronology).slice(-20000).filter(event => event.size >= floor));
  }

  if (includePaper && Array.isArray(paperHistory)) {
    const seen = new Set(), history = [];
    for (const row of paperHistory) {
      if (!row || row.symbol !== symbol || !safeId(row.id) || !positive(row.time) || row.time > until
        || !positive(row.price) || !positive(row.qty) || !acceptsSide(row.side) || !isExecutionStatus(row.status)) continue;
      const id = `paper:${symbol}:${String(row.id)}`;
      if (seen.has(id)) continue;
      seen.add(id);
      history.push({ id, time: row.time, row });
    }
    for (const { id, row } of history.sort(chronology).slice(-200)) {
      const base = { symbol, time: row.time, side: row.side, kind: 'paper', source: 'paper', ...metadata,
        status: cleanText(row.status), role: paperRole(row), historyId: String(row.id) };
      const fills = Array.isArray(row.fills) && row.fills.length > 0 && row.fills.length <= 40 ? row.fills : null;
      const validFills = fills && fills.every(fill => fill && positive(fill.price) && positive(fill.size));
      const filledSize = validFills ? fills.reduce((sum, fill) => sum + fill.size, 0) : 0;
      // Incomplete/corrupt legacy details must not masquerade as the complete
      // set of exact fills. Use the historical average explicitly in that case.
      const exact = validFills && finite(filledSize) && Math.abs(filledSize - row.qty) <= Math.max(1e-12, row.qty * 1e-9);
      if (exact) {
        fills.forEach((fill, index) => {
          if (fill.size >= floor) events.push({ ...base, id: `${id}:${index}`, price: fill.price, size: fill.size,
            averagePriceOnly: false, fillIndex: index, fillCount: fills.length });
        });
      } else if (row.qty >= floor) {
        events.push({ ...base, id: `${id}:average`, price: row.price, size: row.qty, averagePriceOnly: true });
      }
    }
  }
  return events.sort(chronology);
}

/**
 * Logical index for an execution, where integer i is candle i's CENTER.
 * Candle open is i - 0.5; midpoint is i; the next open is i + 0.5.
 * The explicit interval is required: gaps must never stretch a candle to the
 * next observed timestamp. Events in missing buckets return null.
 */
export function executionIndexAtTime(candles, timeMs, intervalSeconds) {
  if (!Array.isArray(candles) || !candles.length || !finite(timeMs) || !positive(intervalSeconds)) return null;
  const durationMs = intervalSeconds * 1000;
  if (!finite(durationMs) || !finite(candles[0]?.time) || timeMs < candles[0].time * 1000) return null;
  let lo = 0, hi = candles.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1, start = candles[mid]?.time;
    if (!finite(start)) return null;
    if (start * 1000 <= timeMs) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  if (found < 0) return null;
  const elapsed = timeMs - candles[found].time * 1000;
  if (elapsed < 0 || elapsed >= durationMs) return null;
  return found - 0.5 + elapsed / durationMs;
}

/**
 * Convert a logical candle coordinate to Unix MILLISECONDS for axis labels.
 * Within the data it uses each actual candle's start, so gaps jump directly to
 * the next observed bucket instead of fabricating intermediate candle times.
 * Only the empty padding before/after the data is extrapolated by the supplied
 * interval; those labels are not evidence of a recorded candle or execution.
 */
export function executionTimeAtIndex(candles, index, intervalSeconds) {
  if (!Array.isArray(candles) || !candles.length || !finite(index) || !positive(intervalSeconds)) return null;
  const durationMs = intervalSeconds * 1000;
  if (!finite(durationMs)) return null;
  const bucket = Math.floor(index + 0.5);
  const anchor = Math.max(0, Math.min(candles.length - 1, bucket));
  const start = candles[anchor]?.time;
  if (!finite(start)) return null;
  const time = start * 1000 + (index - anchor + 0.5) * durationMs;
  return finite(time) ? time : null;
}
