// Observed trade flow and sampled L2 snapshots. No synthetic trades,
// inferred order queues, exchange order routing, or historical MBO data.
const DEFAULT_STEPS = { BTCUSD: 10, ETHUSD: 1, SOLUSD: 0.1 };
const finitePositive = value => Number.isFinite(Number(value)) && Number(value) > 0;
const cloneTrade = trade => ({ ...trade });
const cloneBook = book => book ? { ...book, bids: book.bids.map(level => [...level]), asks: book.asks.map(level => [...level]) } : null;
const cleanText = value => typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 100) : '';
const copyInstrument = value => value && typeof value === 'object' ? { ...value, capabilities: { ...value.capabilities } } : null;
function originalTime(input) {
  const result = {};
  for (const key of ['timeNs', 'timestampNs']) if (typeof input[key] === 'string' && /^\d{15,22}$/.test(input[key])) result[key] = input[key];
  if (Number.isFinite(input.exchangeTime) && input.exchangeTime > 0) result.exchangeTime = input.exchangeTime;
  return result;
}

function compareIds(a, b) {
  if (/^\d+$/.test(a) && /^\d+$/.test(b)) {
    const aa = a.replace(/^0+(?=\d)/, '');
    const bb = b.replace(/^0+(?=\d)/, '');
    return aa.length - bb.length || aa.localeCompare(bb);
  }
  return a.localeCompare(b);
}

function compareTrades(a, b) {
  if (a.time !== b.time) return a.time - b.time;
  const aa = a.timeNs ?? a.timestampNs, bb = b.timeNs ?? b.timestampNs;
  if (aa && bb && BigInt(aa) !== BigInt(bb)) return BigInt(aa) < BigInt(bb) ? -1 : 1;
  return compareIds(String(a.id), String(b.id));
}

function afterIndex(array, time) {
  let lo = 0, hi = array.length;
  while (lo < hi) {
    const middle = (lo + hi) >>> 1;
    if (array[middle].time <= time) lo = middle + 1;
    else hi = middle;
  }
  return lo;
}

function emptySummary(extra = {}) {
  return {
    buyVolume: 0, sellVolume: 0, delta: 0, totalVolume: 0,
    notional: 0, vwap: null, count: 0, from: null, to: null,
    lastPrice: null, buyPercent: null, truncated: false,
    retainedFrom: null, scope: 'retained_observed_trades', ...extra,
  };
}

function summarize(trades, extra = {}) {
  const result = emptySummary(extra);
  for (const trade of trades) {
    if (trade.side === 'buy') result.buyVolume += trade.size;
    else result.sellVolume += trade.size;
    result.notional += trade.price * trade.size;
  }
  result.totalVolume = result.buyVolume + result.sellVolume;
  result.delta = result.buyVolume - result.sellVolume;
  result.count = trades.length;
  result.vwap = result.totalVolume ? result.notional / result.totalVolume : null;
  result.buyPercent = result.totalVolume ? 100 * result.buyVolume / result.totalVolume : null;
  result.from = trades[0]?.time ?? null;
  result.to = trades.at(-1)?.time ?? null;
  result.lastPrice = trades.at(-1)?.price ?? null;
  result.retainedFrom = trades[0]?.time ?? null;
  return result;
}

function resolveStep(step, symbol) {
  return finitePositive(step) ? Number(step) : DEFAULT_STEPS[symbol] || 1;
}

function priceBucket(price, step) {
  // The epsilon avoids placing an exact decimal boundary in the prior bucket.
  return Math.floor(price / step + 1e-9);
}

function bucketPrice(index, step) {
  return Number((index * step).toPrecision(13));
}

function addLevel(map, trade, step) {
  const index = priceBucket(trade.price, step);
  let level = map.get(index);
  if (!level) {
    level = { price: bucketPrice(index, step), buy: 0, sell: 0, volume: 0, delta: 0, count: 0 };
    map.set(index, level);
  }
  level[trade.side] += trade.size;
  level.volume += trade.size;
  level.delta = level.buy - level.sell;
  level.count += 1;
}

function normalizedLevels(levels, descending, limit = Infinity) {
  if (!Array.isArray(levels)) return [];
  const grouped = new Map();
  for (const level of levels) {
    if (!Array.isArray(level) || !finitePositive(level[0]) || !finitePositive(level[1])) continue;
    // A L2 snapshot has one aggregate quantity per price. Duplicate input prices
    // replace that quantity instead of accidentally multiplying liquidity.
    grouped.set(Number(level[0]), Number(level[1]));
  }
  return [...grouped.entries()].sort((a, b) => descending ? b[0] - a[0] : a[0] - b[0]).slice(0, limit);
}

/**
 * A bounded, in-memory record of observed trades and sampled depth.
 * Times and duration are milliseconds. Trade sides are the AGGRESSOR side.
 * The feed adapter, not this store, must invert Coinbase's maker-side field.
 */
export function createFlowStore({ maxTrades = 20000, maxBookFrames = 600 } = {}) {
  if (!Number.isInteger(maxTrades) || maxTrades < 1 || maxTrades > 200000) throw new RangeError('maxTrades must be 1–200000');
  if (!Number.isInteger(maxBookFrames) || maxBookFrames < 1 || maxBookFrames > 36000) throw new RangeError('maxBookFrames must be 1–36000');

  let symbol = null;
  let metadata = { instrument: null, sourceLabel: 'Fonte não informada', environment: '' };
  let trades = [];
  let books = [];
  let tradeIds = new Set();
  let tradeFloor = null;
  let tradeTruncated = false;
  let bookTruncated = false;
  let sessionStartedAt = Date.now();
  let revision = 0;
  let summaryRevision = -1;
  let cachedSummary = emptySummary();

  function reset(nextSymbol, context = {}) {
    symbol = nextSymbol == null ? null : String(nextSymbol);
    const instrument = copyInstrument(context.instrument);
    metadata = { instrument, sourceLabel: cleanText(context.sourceLabel) || (instrument?.provider === 'rithmic' ? 'Rithmic' : DEFAULT_STEPS[symbol] ? 'Coinbase Exchange' : 'Fonte não informada'), environment: cleanText(context.environment) };
    trades = [];
    books = [];
    tradeIds = new Set();
    tradeFloor = null;
    tradeTruncated = false;
    bookTruncated = false;
    sessionStartedAt = Date.now();
    revision++;
  }

  function addTrade(input) {
    if (!input || input.id == null || !finitePositive(input.time) || !finitePositive(input.price) || !finitePositive(input.size)) return false;
    if (input.side !== 'buy' && input.side !== 'sell') return false;
    if (input.symbol && symbol && input.symbol !== symbol) return false;
    if (metadata.instrument && (input.provider != null && input.provider !== metadata.instrument.provider || input.exchange != null && input.exchange !== metadata.instrument.exchange)) return false;
    if (!symbol && input.symbol) { symbol = String(input.symbol); revision++; }
    const trade = {
      symbol: symbol || String(input.symbol || ''), id: String(input.id),
      time: Number(input.time), price: Number(input.price), size: Number(input.size),
      side: input.side, source: input.source === 'backfill' ? 'backfill' : 'live',
      ...(metadata.instrument ? { provider: metadata.instrument.provider, exchange: metadata.instrument.exchange } : {}),
      ...originalTime(input),
    };
    if (!trade.id || tradeIds.has(trade.id) || (tradeFloor && compareTrades(trade, tradeFloor) <= 0)) return false;
    if (!trades.length || compareTrades(trade, trades.at(-1)) > 0) trades.push(trade);
    else {
      let lo = 0, hi = trades.length;
      while (lo < hi) {
        const middle = (lo + hi) >>> 1;
        if (compareTrades(trades[middle], trade) < 0) lo = middle + 1;
        else hi = middle;
      }
      trades.splice(lo, 0, trade);
    }
    tradeIds.add(trade.id);
    if (trades.length > maxTrades) {
      const removed = trades.splice(0, trades.length - maxTrades);
      for (const item of removed) tradeIds.delete(item.id);
      tradeFloor = removed.at(-1);
      tradeTruncated = true;
    }
    revision++;
    return true;
  }

  function addBook(input) {
    if (!input || !input.ready || !finitePositive(input.time)) return false;
    if (input.symbol && symbol && input.symbol !== symbol) return false;
    if (metadata.instrument && (input.provider != null && input.provider !== metadata.instrument.provider || input.exchange != null && input.exchange !== metadata.instrument.exchange)) return false;
    if (!symbol && input.symbol) { symbol = String(input.symbol); revision++; }
    const time = Number(input.time);
    if (books.length && time - books.at(-1).time < 1000) return false;
    const bids = normalizedLevels(input.bids, true, 20);
    const asks = normalizedLevels(input.asks, false, 20);
    if (!bids.length || !asks.length || bids[0][0] >= asks[0][0]) return false;
    const book = { symbol: symbol || String(input.symbol || ''), time, ready: true, bids, asks,
      ...(metadata.instrument ? { provider: metadata.instrument.provider, exchange: metadata.instrument.exchange } : {}), ...originalTime(input) };
    if (input.sequence != null) book.sequence = input.sequence;
    books.push(book);
    if (books.length > maxBookFrames) {
      books.splice(0, books.length - maxBookFrames);
      bookTruncated = true;
    }
    return true;
  }

  function getTrades(limit = 60) {
    const count = Math.min(maxTrades, Math.max(0, Math.floor(Number(limit) || 0)));
    return count ? trades.slice(-count).reverse().map(cloneTrade) : [];
  }

  function getSummary() {
    if (summaryRevision !== revision) {
      cachedSummary = summarize(trades, { symbol, truncated: tradeTruncated, sessionStartedAt });
      summaryRevision = revision;
    }
    return { ...cachedSummary };
  }

  function getProfile({ step, limit = 40 } = {}) {
    step = resolveStep(step, symbol);
    const map = new Map();
    for (const trade of trades) addLevel(map, trade, step);
    const ranked = [...map.values()].sort((a, b) => b.volume - a.volume || b.price - a.price);
    const count = Math.max(1, Math.min(2000, Math.floor(Number(limit) || 40)));
    const summary = getSummary();
    return {
      symbol, step, levels: ranked.slice(0, count).sort((a, b) => b.price - a.price).map(level => ({ ...level })),
      poc: ranked[0]?.price ?? null, totalVolume: summary.totalVolume,
      displayedVolume: ranked.slice(0, count).reduce((sum, level) => sum + level.volume, 0),
      levelCount: ranked.length, omittedLevels: Math.max(0, ranked.length - count),
      from: summary.from, to: summary.to, truncated: tradeTruncated,
      retainedFrom: summary.retainedFrom, scope: summary.scope,
    };
  }

  function getFootprint({ step, bucketSeconds = 60, columns = 6 } = {}) {
    step = resolveStep(step, symbol);
    const seconds = Math.max(1, Math.min(86400, Math.floor(Number(bucketSeconds) || 60)));
    const width = seconds * 1000;
    const count = Math.max(1, Math.min(60, Math.floor(Number(columns) || 6)));
    const summary = getSummary();
    if (!trades.length) return { symbol, step, bucketSeconds: seconds, columns: [], priceLevels: [], from: null, to: null, truncated: tradeTruncated, retainedFrom: null, scope: summary.scope };
    const lastBucket = Math.floor(trades.at(-1).time / width) * width;
    const firstBucket = lastBucket - (count - 1) * width;
    const buckets = new Map();
    for (const trade of trades) {
      if (trade.time < firstBucket) continue;
      const time = Math.floor(trade.time / width) * width;
      let bucket = buckets.get(time);
      if (!bucket) {
        bucket = { time, end: time + width, buy: 0, sell: 0, volume: 0, delta: 0, count: 0, map: new Map() };
        buckets.set(time, bucket);
      }
      addLevel(bucket.map, trade, step);
      bucket[trade.side] += trade.size;
      bucket.volume += trade.size;
      bucket.delta = bucket.buy - bucket.sell;
      bucket.count++;
    }
    const priceSet = new Set();
    const result = [...buckets.values()].sort((a, b) => a.time - b.time).map(({ map, ...bucket }) => {
      const levels = [...map.values()].sort((a, b) => b.price - a.price);
      for (const level of levels) priceSet.add(level.price);
      return { ...bucket, levels };
    });
    // Empty time buckets are absent: no captured trades is not evidence of a
    // zero-volume market interval, particularly across a disconnected feed.
    return {
      symbol, step, bucketSeconds: seconds, columns: result,
      priceLevels: [...priceSet].sort((a, b) => b - a),
      from: result[0]?.time ?? null, to: summary.to,
      truncated: tradeTruncated, retainedFrom: summary.retainedFrom, scope: summary.scope,
    };
  }

  function getRecording() {
    const starts = [trades[0]?.time, books[0]?.time].filter(Number.isFinite);
    const ends = [trades.at(-1)?.time, books.at(-1)?.time].filter(Number.isFinite);
    const from = starts.length ? Math.min(...starts) : null;
    const to = ends.length ? Math.max(...ends) : null;
    return {
      symbol, from, to, trades: trades.length, frames: books.length,
      duration: from != null && to != null ? Math.max(0, to - from) : 0,
      truncated: tradeTruncated || bookTruncated, tradeTruncated, bookTruncated,
      retainedFrom: from, tradesFrom: trades[0]?.time ?? null, booksFrom: books[0]?.time ?? null,
      sessionStartedAt, maxTrades, maxBookFrames, bookSampleIntervalMs: 1000,
      bookDepth: 20, source: metadata.sourceLabel, provider: metadata.instrument?.provider ?? (DEFAULT_STEPS[symbol] ? 'coinbase' : null),
      exchange: metadata.instrument?.exchange ?? (DEFAULT_STEPS[symbol] ? 'COINBASE' : null), environment: metadata.environment, timeUnit: 'milliseconds',
    };
  }

  function getAt(time) {
    const timestamp = Number(time);
    if (!Number.isFinite(timestamp)) return { time: null, trades: [], book: null, summary: emptySummary({ symbol }), price: null };
    const before = trades.slice(0, afterIndex(trades, timestamp));
    const bookIndex = afterIndex(books, timestamp) - 1;
    const summary = summarize(before, { symbol, truncated: tradeTruncated, sessionStartedAt });
    return {
      time: timestamp, trades: before.slice(-60).reverse().map(cloneTrade),
      book: cloneBook(bookIndex >= 0 ? books[bookIndex] : null), summary,
      price: before.at(-1)?.price ?? null,
    };
  }

  function exportData() {
    return {
      format: 'qhos-observed-flow-v1', exportedAt: new Date().toISOString(),
      source: metadata.sourceLabel === 'Coinbase Exchange' ? 'Coinbase Exchange public market data' : metadata.sourceLabel,
      sourceLabel: metadata.sourceLabel, environment: metadata.environment, instrument: copyInstrument(metadata.instrument),
      sourceVerified: false, origin: 'browser-session',
      symbol, recording: getRecording(), summary: getSummary(),
      method: {
        tradeSide: 'aggressor', tradeSizeUnit: metadata.instrument?.quantityUnit ?? 'base asset', priceUnit: metadata.instrument?.currency ?? 'USD',
        timeUnit: 'milliseconds', originalTimestamp: 'Nanosecond strings retained when supplied; chart coordinates use milliseconds.',
        trades: 'Observed live matches and explicitly marked REST backfills; not a guaranteed complete exchange history.',
        book: 'L2 quantities aggregated by price; first valid snapshot at least 1000 ms after the prior sample, at most 20 bids and 20 asks.',
        replay: 'Last captured event and sampled book at or before the selected time; no interpolation.',
        limits: 'Bounded observed window; no MBO, individual queue positions, or continuity guarantee. In-memory capture resets on reload; QHOS v3 checkpoints and saved session archives preserve only the retained window.',
      },
      trades: trades.map(cloneTrade), books: books.map(cloneBook),
    };
  }

  return { reset, addTrade, addBook, getTrades, getSummary, getProfile, getFootprint, getRecording, getAt, exportData };
}

/**
 * A hypothetical fill against visible aggregate depth, never a real order.
 * Caller must enforce feed freshness and account/risk checks before using it.
 * Any unfilled remainder is returned, never invented at the last seen price.
 */
export function simulateMarketFill(side, quantity, book, { limitPrice } = {}) {
  if (side !== 'buy' && side !== 'sell') throw new TypeError('side must be buy or sell');
  if (!finitePositive(quantity)) throw new RangeError('quantity must be positive');
  if (limitPrice != null && !finitePositive(limitPrice)) throw new RangeError('limitPrice must be positive');
  const requested = Number(quantity);
  const empty = reason => ({ filled: 0, remaining: requested, averagePrice: null, notional: 0, levels: [], complete: false, reason });
  if (!book?.ready) return empty('book_not_ready');
  const bids = normalizedLevels(book.bids, true);
  const asks = normalizedLevels(book.asks, false);
  if (!bids.length || !asks.length || bids[0][0] >= asks[0][0]) return empty('invalid_book');
  const available = side === 'buy' ? asks : bids;
  let remaining = requested, filled = 0, notional = 0;
  const levels = [];
  for (const [price, size] of available) {
    if (limitPrice != null && (side === 'buy' ? price > Number(limitPrice) : price < Number(limitPrice))) break;
    const fill = Math.min(remaining, size);
    if (fill <= 0) break;
    levels.push({ price, size: fill, notional: price * fill });
    filled += fill;
    notional += price * fill;
    remaining = Math.max(0, requested - filled);
    if (remaining <= Math.min(requested * 1e-12, 1e-10)) { remaining = 0; break; }
  }
  return {
    filled, remaining, averagePrice: filled ? notional / filled : null,
    notional, levels, complete: remaining === 0,
    reason: remaining ? (limitPrice != null ? 'limit_or_visible_depth' : 'insufficient_visible_depth') : null,
  };
}
