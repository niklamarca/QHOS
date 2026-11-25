import { displayTime } from 'qhos-module/market.js';

// Flow visualizations use observed trades / aggregate L2 snapshots only.
// They do not infer individual orders, hidden liquidity, or exchange continuity.
const GAP_MS = 2500;
const COLORS = {
  background: '#0d1116', text: '#dbe4ee', muted: '#758392', grid: '#202a35',
  lime: '#b9f58b', bid: '#24c9a0', ask: '#dba75c', down: '#ec6573',
};
const finite = value => typeof value === 'number' && Number.isFinite(value);
const positive = value => finite(Number(value)) && Number(value) > 0;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const integer = (value, fallback, low, high) => clamp(Math.floor(Number(value) || fallback), low, high);
const priceIndex = (price, step) => Math.floor(price / step + 1e-9);
const bucketPrice = (index, step) => Number((index * step).toPrecision(13));
const defaultQuantity = value => finite(value) ? new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 5, notation: Math.abs(value) >= 10000 ? 'compact' : 'standard' }).format(value) : '—';
const defaultPrice = value => finite(value) ? new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 5 }).format(value) : '—';
const clock = (time, seconds = true) => displayTime.time(time, { seconds });
const dateClock = time => displayTime.dateTime(time);

function normalizedTrades(input) {
  const seen = new Set(), trades = [];
  let invalid = 0, duplicates = 0;
  for (const trade of Array.isArray(input) ? input : []) {
    if (!trade || !positive(trade.time) || !positive(trade.size) || !['buy', 'sell'].includes(trade.side)) { invalid++; continue; }
    const key = trade.id == null ? null : `${trade.symbol || ''}:${String(trade.id)}`;
    if (key !== null && seen.has(key)) { duplicates++; continue; }
    if (key !== null) seen.add(key);
    trades.push({ ...trade, time: Number(trade.time), size: Number(trade.size) });
  }
  trades.sort((a, b) => {
    if (a.time !== b.time) return a.time - b.time;
    const aa = String(a.id ?? ''), bb = String(b.id ?? '');
    if (/^\d+$/.test(aa) && /^\d+$/.test(bb) && aa.length !== bb.length) return aa.length - bb.length;
    return aa === bb ? 0 : aa < bb ? -1 : 1;
  });
  return { trades, invalid, duplicates };
}

function normalizedGaps(input, start, end) {
  return (Array.isArray(input) ? input : []).map(gap => {
    const from = Number(gap?.from ?? gap?.start);
    const to = gap?.to == null && gap?.end == null ? end : Number(gap.to ?? gap.end);
    return { from: Math.max(start, from), to: Math.min(end, to), kind: 'feed_gap', known: true, reason: String(gap?.reason || 'Interrupção registrada da conexão') };
  }).filter(gap => finite(gap.from) && finite(gap.to) && gap.to > gap.from).sort((a, b) => a.from - b.from);
}

/**
 * CVD units are base-asset quantity, with aggressor buy positive / sell negative.
 * `from` crops the view, preserving the observed pre-view baseline. `resetAt`
 * discards earlier observations and starts a new baseline at zero. Neither
 * option invents missing history. Feed gaps may be supplied by the caller;
 * intervals without trades are separately labeled, not classified as feed loss.
 */
export function computeCVD(input, { bucketSeconds = 5, until = Infinity, from = -Infinity, resetAt = null, gaps = [], gapThresholdMs = GAP_MS } = {}) {
  const normalized = normalizedTrades(input);
  const width = integer(bucketSeconds, 5, 1, 86400) * 1000;
  const threshold = Math.max(1, Number(gapThresholdMs) || GAP_MS);
  const endLimit = finite(until) ? until : Infinity;
  const explicitReset = finite(resetAt);
  const all = normalized.trades.filter(trade => trade.time <= endLimit);
  const reset = explicitReset ? resetAt : all[0]?.time ?? null;
  const retained = all.filter(trade => reset === null || trade.time >= reset);
  const requestedFrom = finite(from) ? from : retained[0]?.time ?? (finite(endLimit) ? endLimit : null);
  const viewFrom = requestedFrom === null ? null : Math.max(requestedFrom, explicitReset ? resetAt : -Infinity);
  const viewTo = finite(endLimit) ? Math.max(viewFrom ?? endLimit, endLimit) : retained.at(-1)?.time ?? viewFrom;
  const feedGaps = viewFrom === null || viewTo === null ? [] : normalizedGaps(gaps, viewFrom, viewTo);
  const observationGaps = [];
  for (let i = 1; i < retained.length; i++) {
    if (retained[i].time - retained[i - 1].time <= threshold) continue;
    const gap = { from: Math.max(viewFrom ?? -Infinity, retained[i - 1].time), to: Math.min(viewTo ?? Infinity, retained[i].time), kind: 'no_observed_trades', known: false, reason: 'Intervalo sem negócios observados; não comprova falha do feed' };
    if (gap.to > gap.from) observationGaps.push(gap);
  }
  if (retained.length && viewFrom !== null && retained[0].time - viewFrom > threshold) observationGaps.unshift({ from: viewFrom, to: Math.min(viewTo, retained[0].time), kind: 'no_observed_trades', known: false, reason: 'Nenhum negócio disponível antes da primeira observação' });
  if (retained.length && viewTo !== null && viewTo - retained.at(-1).time > threshold) observationGaps.push({ from: Math.max(viewFrom, retained.at(-1).time), to: viewTo, kind: 'no_observed_trades', known: false, reason: 'Sem novos negócios observados; não comprova falha do feed' });
  let cumulative = 0, baseline = 0, visibleTradeCount = 0;
  const buckets = new Map();
  for (const trade of retained) {
    const signed = trade.side === 'buy' ? trade.size : -trade.size;
    cumulative += signed;
    if (viewFrom !== null && trade.time < viewFrom) { baseline += signed; continue; }
    visibleTradeCount++;
    const time = Math.floor(trade.time / width) * width;
    let point = buckets.get(time);
    if (!point) {
      point = { time, end: Math.min(time + width, finite(endLimit) ? endLimit : time + width), firstTime: trade.time, lastTime: trade.time, buy: 0, sell: 0, delta: 0, cumulative: 0, count: 0, breakBefore: false };
      buckets.set(time, point);
    }
    point[trade.side] += trade.size;
    point.delta = point.buy - point.sell;
    point.cumulative = cumulative;
    point.count++;
    point.lastTime = trade.time;
  }
  const points = [...buckets.values()];
  const plotGaps = [...feedGaps, ...observationGaps].sort((a, b) => a.from - b.from || Number(b.known) - Number(a.known));
  let gapIndex = 0;
  for (let i = 0; i < points.length; i++) {
    const previous = points[i - 1];
    const start = previous?.lastTime ?? points[i].firstTime;
    while (gapIndex < plotGaps.length && plotGaps[gapIndex].to <= start) gapIndex++;
    points[i].breakBefore = !previous || (gapIndex < plotGaps.length && plotGaps[gapIndex].from < points[i].lastTime);
    points[i].partial = points[i].time < (viewFrom ?? points[i].time) || points[i].end < points[i].time + width;
  }
  const symbols = [...new Set(all.map(trade => trade.symbol).filter(Boolean))];
  return {
    type: 'cvd', symbol: symbols.length === 1 ? symbols[0] : null, points, baseline, finalValue: cumulative,
    from: viewFrom, to: viewTo, bucketSeconds: width / 1000,
    coverage: {
      observedFrom: retained[0]?.time ?? null, observedTo: retained.at(-1)?.time ?? null,
      tradeCount: retained.length, visibleTradeCount, complete: false,
      resetAt: reset, explicitReset, baselinePartial: !explicitReset || (all[0]?.time ?? Infinity) > resetAt,
      baselineMode: explicitReset ? 'explicit_reset' : 'first_available_observation',
      gaps: plotGaps, feedGaps, observationGaps, gapThresholdMs: threshold,
      invalid: normalized.invalid, duplicates: normalized.duplicates, symbols,
      note: 'CVD sobre negócios disponíveis; ausência de negócios não comprova interrupção ou perda. Sem histórico anterior presumido.',
    },
  };
}

function normalizedLevels(input, descending) {
  const map = new Map();
  for (const level of Array.isArray(input) ? input : []) {
    if (!Array.isArray(level) || !positive(level[0]) || !positive(level[1])) continue;
    map.set(Number(level[0]), Number(level[1]));
  }
  return [...map].sort((a, b) => descending ? b[0] - a[0] : a[0] - b[0]);
}

function normalizedBooks(input, until) {
  const map = new Map();
  let invalid = 0;
  for (const book of Array.isArray(input) ? input : []) {
    if (!book || book.ready === false || !positive(book.time)) { invalid++; continue; }
    const time = Number(book.time);
    if (time > until) continue;
    const bids = normalizedLevels(book.bids, true), asks = normalizedLevels(book.asks, false);
    if (!bids.length || !asks.length || bids[0][0] >= asks[0][0]) { invalid++; continue; }
    map.set(time, { time, symbol: book.symbol, bids, asks, mid: (bids[0][0] + asks[0][0]) / 2 });
  }
  return { books: [...map.values()].sort((a, b) => a.time - b.time), invalid };
}

/**
 * Heatmap quantities are displayed L2 liquidity, not executed volume. When
 * temporal downsampling is needed, a cell is the arithmetic mean of snapshots
 * in that column where that side/price was within recorded depth. Null means
 * unrecorded depth; zero means observed depth without quantity in that bucket.
 * No column or midpoint line bridges a source sample gap above 2.5 seconds.
 */
export function computeHeatmap(input, { step, until = Infinity, windowSeconds = 180, maxRows = 48, maxColumns = 180, gapThresholdMs = GAP_MS } = {}) {
  const threshold = Math.max(1, Number(gapThresholdMs) || GAP_MS);
  const endLimit = finite(until) ? until : Infinity;
  const normalized = normalizedBooks(input, endLimit);
  const all = normalized.books;
  const rowLimit = integer(maxRows, 48, 1, 160), columnLimit = integer(maxColumns, 180, 1, 600);
  const windowMs = integer(windowSeconds, 180, 1, 86400) * 1000;
  const to = finite(endLimit) ? endLimit : all.at(-1) ? all.at(-1).time + 1000 : null;
  const from = to === null ? null : to - windowMs;
  const selected = from === null ? [] : all.filter(book => book.time >= from - threshold && book.time <= to);
  const priceStep = positive(step) ? Number(step) : (selected.at(-1)?.mid ?? 0) >= 10000 ? 10 : (selected.at(-1)?.mid ?? 0) >= 100 ? 1 : 0.1;
  const base = {
    type: 'heatmap', symbol: selected.at(-1)?.symbol ?? null, rows: [], columns: [], midpoints: [], from, to, step: priceStep, maxQuantity: 0,
    coverage: { observedFrom: null, observedTo: null, sampleCount: 0, displayedSamples: 0, complete: false, gaps: [], invalid: normalized.invalid, aggregation: 'mean_of_observed_snapshots', gapThresholdMs: threshold, croppedRows: false, omittedColumns: 0, note: 'Liquidez L2 visível nos snapshots; não representa negócios executados ou ordens individuais.' },
  };
  if (!selected.length) return base;

  const intervals = selected.slice(1).map((book, i) => book.time - selected[i].time).filter(duration => duration > 0 && duration <= threshold).sort((a, b) => a - b);
  const sampleInterval = clamp(intervals.length ? intervals[Math.floor(intervals.length / 2)] : 1000, 1, threshold);
  const prepared = [];
  const gaps = [];
  for (let i = 0; i < selected.length; i++) {
    const book = selected[i], next = selected[i + 1];
    const disconnected = next && next.time - book.time > threshold;
    const end = Math.min(to, next && !disconnected ? next.time : book.time + sampleInterval);
    if (end <= from || end < book.time) continue;
    const bidBuckets = new Map(), askBuckets = new Map();
    for (const [price, size] of book.bids) { const key = priceIndex(price, priceStep); bidBuckets.set(key, (bidBuckets.get(key) || 0) + size); }
    for (const [price, size] of book.asks) { const key = priceIndex(price, priceStep); askBuckets.set(key, (askBuckets.get(key) || 0) + size); }
    prepared.push({ ...book, end, bidBuckets, askBuckets, minBid: Math.min(...bidBuckets.keys()), maxAsk: Math.max(...askBuckets.keys()), breakBefore: !i || book.time - selected[i - 1].time > threshold });
    if (disconnected) gaps.push({ from: Math.max(from, end), to: Math.min(to, next.time), kind: 'no_book_samples', known: true, reason: 'Sem snapshots L2 neste intervalo' });
  }
  if (!prepared.length) return base;
  if (prepared[0].time > from) gaps.unshift({ from, to: prepared[0].time, kind: 'no_book_samples', known: true, reason: 'Sem snapshot gravado neste intervalo' });
  if (prepared.at(-1).end < to) gaps.push({ from: prepared.at(-1).end, to, kind: 'no_book_samples', known: true, reason: 'Sem novo snapshot gravado neste intervalo' });

  let low = Math.min(...prepared.map(book => book.minBid));
  let high = Math.max(...prepared.map(book => book.maxAsk));
  const observedLow = low, observedHigh = high;
  const rowCount = Math.min(rowLimit, high - low + 1);
  if (high - low + 1 > rowLimit) {
    const center = priceIndex(prepared.at(-1).mid, priceStep);
    low = clamp(center - Math.floor(rowLimit / 2), observedLow, observedHigh - rowLimit + 1);
    high = low + rowLimit - 1;
  }
  const indices = Array.from({ length: rowCount }, (_, index) => high - index);
  const rows = indices.map(index => bucketPrice(index, priceStep));
  const binWidth = windowMs / columnLimit;
  const groups = [];
  for (const book of prepared) {
    const bin = Math.floor((Math.max(from, book.time) - from) / binWidth);
    const group = groups.at(-1);
    if (group && group.bin === bin && !book.breakBefore) group.frames.push(book);
    else groups.push({ bin, frames: [book] });
  }
  // Pathological isolated samples can produce more groups than bins. Retain the
  // latest groups rather than merging observations across missing intervals.
  const retainedGroups = groups.slice(-columnLimit);
  let maximum = 0;
  const columns = retainedGroups.map(({ frames }, index) => {
    const bids = [], asks = [], bidSamples = [], askSamples = [];
    for (const price of indices) {
      let bid = 0, ask = 0, bidCount = 0, askCount = 0;
      for (const frame of frames) {
        if (price >= frame.minBid) { bid += frame.bidBuckets.get(price) || 0; bidCount++; }
        if (price <= frame.maxAsk) { ask += frame.askBuckets.get(price) || 0; askCount++; }
      }
      bids.push(bidCount ? bid / bidCount : null); asks.push(askCount ? ask / askCount : null);
      bidSamples.push(bidCount); askSamples.push(askCount);
      maximum = Math.max(maximum, bidCount ? bid / bidCount : 0, askCount ? ask / askCount : 0);
    }
    return {
      time: frames[0].time, end: frames.at(-1).end, firstTime: frames[0].time, lastTime: frames.at(-1).time,
      bids, asks, bidSamples, askSamples, sampleCount: frames.length, mid: frames.at(-1).mid,
      breakBefore: !index || frames[0].breakBefore, aggregation: frames.length > 1 ? 'mean' : 'snapshot',
    };
  });
  return {
    ...base, rows, columns, maxQuantity: maximum,
    midpoints: prepared.map(book => ({ time: book.time, mid: book.mid, breakBefore: book.breakBefore })),
    coverage: {
      ...base.coverage, observedFrom: prepared[0].time, observedTo: prepared.at(-1).time,
      sampleCount: prepared.length, displayedSamples: retainedGroups.reduce((sum, group) => sum + group.frames.length, 0),
      gaps: gaps.filter(gap => gap.to > gap.from), sampleIntervalMs: sampleInterval,
      croppedRows: high !== observedHigh || low !== observedLow,
      observedPriceFrom: bucketPrice(observedLow, priceStep), observedPriceTo: bucketPrice(observedHigh + 1, priceStep),
      displayedPriceFrom: bucketPrice(low, priceStep), displayedPriceTo: bucketPrice(high + 1, priceStep),
      omittedColumns: Math.max(0, groups.length - columns.length),
    },
  };
}

let rendererId = 0;

/** Responsive, dependency-free, accessible canvas with hover/pinned inspection. */
export class FlowCanvas {
  constructor(container, { type = 'cvd', formatPrice = defaultPrice, formatQuantity = defaultQuantity, onHover, ...options } = {}) {
    if (!container || typeof container.appendChild !== 'function') throw new TypeError('FlowCanvas requires a DOM container');
    this.container = container;
    this.type = type === 'heatmap' ? 'heatmap' : 'cvd';
    this.formatPrice = formatPrice;
    this.formatQuantity = formatQuantity;
    this.onHover = onHover;
    this.options = { showDeltaBars: true, showMidline: true, logScale: true, ...options };
    this.data = null;
    this.hover = null;
    this.pointer = null;
    this.pinned = false;
    this.frame = null;
    this.destroyed = false;
    this.originalPosition = container.style.position;
    this.originalMinHeight = container.style.minHeight;
    const computedStyle = getComputedStyle(container);
    if (computedStyle.position === 'static') container.style.position = 'relative';
    if (container.getBoundingClientRect().height < 100 && !(parseFloat(computedStyle.height) > 0)) container.style.minHeight = `${Number(options.height) || 240}px`;
    this.canvas = document.createElement('canvas');
    this.canvas.style.cssText = 'display:block;width:100%;height:100%;outline:none;touch-action:pan-y;border-radius:8px;';
    this.canvas.tabIndex = 0;
    this.canvas.setAttribute('role', 'img');
    this.overlay = document.createElement('canvas');
    this.overlay.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;border-radius:8px;';
    this.overlay.setAttribute('aria-hidden', 'true');
    this.tooltip = document.createElement('div');
    this.tooltip.style.cssText = 'position:absolute;z-index:4;display:none;max-width:260px;min-width:165px;padding:11px 13px;background:rgba(19,25,33,.97);border:1px solid #354151;border-radius:8px;box-shadow:0 8px 28px #0006;color:#dbe4ee;font:11px/1.5 "DM Sans",sans-serif;pointer-events:none;';
    this.tooltip.setAttribute('role', 'tooltip');
    this.tooltip.id = `qhos-flow-tip-${++rendererId}`;
    this.canvas.setAttribute('aria-describedby', this.tooltip.id);
    container.append(this.canvas, this.overlay, this.tooltip);
    this.context = this.canvas.getContext('2d');
    this.overlayContext = this.overlay.getContext('2d');
    this.handlers = {
      pointermove: event => { if (!this.pinned) { this.pointer = this.coordinates(event); this.inspectPointer(); } },
      pointerleave: () => { if (!this.pinned) { this.pointer = null; this.clearInspection(); } },
      click: event => {
        this.canvas.focus({ preventScroll: true });
        const pointer = this.coordinates(event), candidate = this.selectionAt(pointer.x, pointer.y);
        if (!candidate) { this.pinned = false; this.clearInspection(); return; }
        const same = this.pinned && this.hover?.key === candidate.key;
        this.pinned = !same; this.pointer = pointer; this.hover = candidate;
        this.showInspection();
      },
      keydown: event => this.onKey(event),
      focus: () => { this.canvas.style.boxShadow = 'inset 0 0 0 1px #b9f58b66'; },
      blur: () => { this.canvas.style.boxShadow = ''; if (!this.pinned) this.clearInspection(); },
    };
    for (const [name, handler] of Object.entries(this.handlers)) this.canvas.addEventListener(name, handler);
    this.observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => this.scheduleDraw()) : null;
    this.observer?.observe(container);
    this.windowResize = () => this.scheduleDraw();
    globalThis.addEventListener('resize', this.windowResize);
    this.draw();
  }

  setData(data) {
    if (this.destroyed) return;
    if ((this.data?.symbol && data?.symbol && this.data.symbol !== data.symbol) || (data?.type && data.type !== this.type)) this.clearInspection(true);
    if (data?.type === 'cvd' || data?.type === 'heatmap') this.type = data.type;
    this.data = data;
    this.scheduleDraw();
  }

  setOptions(options = {}) {
    if (this.destroyed) return;
    this.options = { ...this.options, ...options };
    if (typeof options.formatPrice === 'function') this.formatPrice = options.formatPrice;
    if (typeof options.formatQuantity === 'function') this.formatQuantity = options.formatQuantity;
    if (positive(options.height)) this.container.style.minHeight = `${Number(options.height)}px`;
    this.scheduleDraw();
  }

  refreshTimeZone() {
    // Redraw the retained result and any pinned tooltip without recomputing buckets.
    this.scheduleDraw();
  }

  scheduleDraw() {
    if (this.destroyed || this.frame !== null) return;
    this.frame = requestAnimationFrame(() => { this.frame = null; this.draw(); });
  }

  resize() {
    const rect = this.container.getBoundingClientRect();
    this.width = Math.max(1, rect.width);
    this.height = Math.max(180, rect.height || Number(this.options.height) || 240);
    const dpr = Math.max(1, Math.min(3, globalThis.devicePixelRatio || 1));
    for (const canvas of [this.canvas, this.overlay]) {
      const width = Math.round(this.width * dpr), height = Math.round(this.height * dpr);
      if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    }
    this.context.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.overlayContext.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.plot = { left: this.width < 400 ? 57 : 72, right: this.width - 15, top: 34, bottom: this.height - 34 };
    this.plot.width = Math.max(1, this.plot.right - this.plot.left);
    this.plot.height = Math.max(1, this.plot.bottom - this.plot.top);
  }

  draw() {
    if (this.destroyed || !this.context) return;
    this.resize();
    const c = this.context, d = this.data;
    c.clearRect(0, 0, this.width, this.height);
    c.fillStyle = COLORS.background; c.fillRect(0, 0, this.width, this.height);
    c.font = '10px "JetBrains Mono",monospace'; c.textBaseline = 'middle';
    this.drawHeader();
    const hasData = this.type === 'cvd' ? d?.points?.length : d?.columns?.length && d?.rows?.length;
    if (!hasData || !finite(d.from) || !finite(d.to)) {
      this.xFor = null; this.yFor = null;
      c.fillStyle = COLORS.text; c.font = '500 12px "DM Sans",sans-serif'; c.textAlign = 'center';
      c.fillText(this.type === 'cvd' ? 'Aguardando negócios observados' : 'Aguardando snapshots de liquidez', this.width / 2, this.height / 2 - 7);
      c.fillStyle = COLORS.muted; c.font = '11px "DM Sans",sans-serif';
      c.fillText('O gráfico aparece com a captura desta sessão.', this.width / 2, this.height / 2 + 15);
      this.canvas.setAttribute('aria-label', this.type === 'cvd' ? 'CVD sem negócios disponíveis.' : 'Heatmap sem snapshots disponíveis.');
      this.showInspection(false); return;
    }
    const minSpan = this.type === 'cvd' ? Math.max(1000, d.bucketSeconds * 1000) : 1000;
    this.axisFrom = d.from;
    this.axisTo = Math.max(d.to, d.from + minSpan);
    this.xFor = time => this.plot.left + (time - this.axisFrom) / (this.axisTo - this.axisFrom) * this.plot.width;
    if (this.type === 'cvd') this.drawCVD(); else this.drawHeatmap();
    this.drawTimeAxis();
    if (!this.pinned && this.pointer) this.hover = this.selectionAt(this.pointer.x, this.pointer.y);
    this.showInspection(false);
  }

  drawHeader() {
    const c = this.context;
    c.textAlign = 'left'; c.font = '600 10px "DM Sans",sans-serif'; c.fillStyle = COLORS.muted;
    c.fillText(this.type === 'cvd' ? 'DELTA ACUMULADO' : 'LIQUIDEZ VISÍVEL · L2', 14, 16);
    if (this.type === 'cvd' && finite(this.data?.finalValue)) {
      c.textAlign = 'right'; c.fillStyle = this.data.finalValue >= 0 ? COLORS.lime : COLORS.down;
      c.font = '500 12px "JetBrains Mono",monospace';
      c.fillText(`${this.data.finalValue > 0 ? '+' : ''}${this.formatQuantity(this.data.finalValue)}`, this.width - 15, 16);
    } else if (this.type === 'heatmap' && this.width > 290) {
      const left = this.width - 113;
      c.fillStyle = COLORS.bid; c.fillRect(left, 12, 6, 6); c.fillStyle = COLORS.muted; c.fillText('BID', left + 11, 16);
      c.fillStyle = COLORS.ask; c.fillRect(left + 53, 12, 6, 6); c.fillStyle = COLORS.muted; c.fillText('ASK', left + 64, 16);
    }
  }

  drawGaps(top, bottom) {
    const c = this.context;
    for (const gap of this.data.coverage?.gaps || []) {
      const left = clamp(this.xFor(gap.from), this.plot.left, this.plot.right), right = clamp(this.xFor(gap.to), this.plot.left, this.plot.right);
      if (right <= left) continue;
      c.fillStyle = gap.kind === 'feed_gap' ? 'rgba(236,101,115,.065)' : 'rgba(117,131,146,.035)';
      c.fillRect(left, top, right - left, bottom - top);
      c.strokeStyle = gap.kind === 'feed_gap' ? '#ec657344' : '#75839225'; c.setLineDash([2, 4]); c.lineWidth = 1;
      c.beginPath(); c.moveTo(left, top); c.lineTo(left, bottom); c.stroke(); c.setLineDash([]);
    }
  }

  drawCVD() {
    const c = this.context, d = this.data, p = this.plot;
    const bars = this.options.showDeltaBars && p.height > 120;
    const bottom = bars ? p.top + p.height * .69 : p.bottom;
    this.cvdBottom = bottom;
    let low = Math.min(0, d.baseline, ...d.points.map(point => point.cumulative));
    let high = Math.max(0, d.baseline, ...d.points.map(point => point.cumulative));
    const padding = Math.max((high - low) * .1, Math.abs(high || low) * .04, 1e-8);
    low -= padding; high += padding;
    this.yFor = value => bottom - (value - low) / (high - low) * (bottom - p.top);
    c.font = '9px "JetBrains Mono",monospace';
    for (let i = 0; i <= 4; i++) {
      const y = p.top + i * (bottom - p.top) / 4, value = high - i * (high - low) / 4;
      c.strokeStyle = COLORS.grid; c.lineWidth = .6; c.beginPath(); c.moveTo(p.left, y); c.lineTo(p.right, y); c.stroke();
      c.fillStyle = COLORS.muted; c.textAlign = 'right'; c.fillText(this.formatQuantity(value), p.left - 9, y);
    }
    this.drawGaps(p.top, p.bottom);
    c.save(); c.beginPath(); c.rect(p.left, p.top, p.width, bottom - p.top); c.clip();
    const zero = this.yFor(0);
    c.strokeStyle = '#75839266'; c.setLineDash([3, 5]); c.beginPath(); c.moveTo(p.left, zero); c.lineTo(p.right, zero); c.stroke(); c.setLineDash([]);
    const segments = [];
    for (const point of d.points) {
      if (!segments.length || point.breakBefore) segments.push([]);
      segments.at(-1).push(point);
    }
    for (const segment of segments) {
      c.beginPath();
      segment.forEach((point, index) => {
        const x = this.xFor(point.lastTime), y = this.yFor(point.cumulative);
        if (!index) c.moveTo(x, y);
        else { c.lineTo(x, this.yFor(segment[index - 1].cumulative)); c.lineTo(x, y); }
      });
      c.strokeStyle = COLORS.lime; c.lineWidth = 1.8; c.lineJoin = 'round'; c.stroke();
      if (segment.length > 1) {
        c.lineTo(this.xFor(segment.at(-1).lastTime), zero); c.lineTo(this.xFor(segment[0].lastTime), zero); c.closePath();
        const gradient = c.createLinearGradient(0, p.top, 0, bottom); gradient.addColorStop(0, '#b9f58b16'); gradient.addColorStop(1, '#b9f58b02'); c.fillStyle = gradient; c.fill();
      }
    }
    for (const point of d.points) if (point.breakBefore || d.points.length === 1) {
      c.fillStyle = COLORS.lime; c.beginPath(); c.arc(this.xFor(point.lastTime), this.yFor(point.cumulative), 2.1, 0, Math.PI * 2); c.fill();
    }
    c.restore();
    if (bars) {
      const barTop = bottom + 17, barBottom = p.bottom - 1, middle = (barTop + barBottom) / 2;
      const maximum = Math.max(1e-12, ...d.points.map(point => Math.abs(point.delta)));
      c.strokeStyle = COLORS.grid; c.beginPath(); c.moveTo(p.left, middle); c.lineTo(p.right, middle); c.stroke();
      for (const point of d.points) {
        const start = clamp(this.xFor(Math.max(d.from, point.time)), p.left, p.right);
        const end = clamp(this.xFor(Math.min(d.to, point.end)), p.left, p.right);
        const height = Math.abs(point.delta) / maximum * Math.max(1, (barBottom - barTop) / 2 - 2);
        c.fillStyle = point.delta >= 0 ? '#24c9a080' : '#ec657380';
        c.fillRect(start + 1, point.delta >= 0 ? middle - height : middle, Math.max(1, end - start - 2), Math.max(.7, height));
      }
      c.textAlign = 'right'; c.fillStyle = COLORS.muted; c.font = '8px "JetBrains Mono",monospace'; c.fillText(`Δ ${d.bucketSeconds}s`, p.left - 9, middle);
    }
    this.canvas.setAttribute('aria-label', `Delta acumulado dos negócios observados: ${this.formatQuantity(d.finalValue)}. ${d.coverage.visibleTradeCount} negócios visíveis. Base observada ${this.formatQuantity(d.baseline)}. ${displayTime.range(d.from, d.to)}. Use setas para inspecionar, Enter para fixar e Escape para liberar. Intervalos sem negócios não comprovam falha do feed.`);
  }

  drawHeatmap() {
    const c = this.context, d = this.data, p = this.plot;
    const cellHeight = p.height / d.rows.length;
    this.yFor = price => p.top + (d.rows[0] + d.step - price) / (d.rows.length * d.step) * p.height;
    const intensity = quantity => this.options.logScale ? Math.log1p(quantity) / Math.log1p(d.maxQuantity || 1) : quantity / (d.maxQuantity || 1);
    c.save(); c.beginPath(); c.rect(p.left, p.top, p.width, p.height); c.clip();
    for (const column of d.columns) {
      const rawLeft = Math.max(p.left, this.xFor(column.time)), right = Math.min(p.right, this.xFor(column.end));
      if (right < rawLeft) continue;
      const left = right === rawLeft ? Math.max(p.left, rawLeft - 1) : rawLeft;
      for (let row = 0; row < d.rows.length; row++) {
        const bid = column.bids[row], ask = column.asks[row], y = p.top + row * cellHeight;
        const both = bid > 0 && ask > 0;
        if (bid > 0) { c.fillStyle = `rgba(36,201,160,${.07 + .8 * clamp(intensity(bid), 0, 1)})`; c.fillRect(left, y + (both ? cellHeight / 2 : 0), Math.max(.7, right - left), both ? cellHeight / 2 : cellHeight); }
        if (ask > 0) { c.fillStyle = `rgba(219,167,92,${.07 + .8 * clamp(intensity(ask), 0, 1)})`; c.fillRect(left, y, Math.max(.7, right - left), both ? cellHeight / 2 : cellHeight); }
      }
    }
    this.drawGaps(p.top, p.bottom);
    if (this.options.showMidline) {
      c.strokeStyle = '#e2eaf0b3'; c.lineWidth = 1.25; c.beginPath();
      let previous = null;
      for (const point of d.midpoints || []) {
        const x = this.xFor(point.time), y = this.yFor(point.mid);
        if (!previous || point.breakBefore) c.moveTo(x, y);
        else { c.lineTo(x, this.yFor(previous.mid)); c.lineTo(x, y); }
        previous = point;
      }
      c.stroke();
    }
    c.restore();
    c.textAlign = 'right'; c.font = '9px "JetBrains Mono",monospace'; c.fillStyle = COLORS.muted;
    const stride = Math.max(1, Math.ceil(d.rows.length / 6));
    for (let index = 0; index < d.rows.length; index += stride) {
      const y = p.top + (index + .5) * cellHeight;
      c.strokeStyle = '#2b354333'; c.lineWidth = .7; c.beginPath(); c.moveTo(p.left, y); c.lineTo(p.right, y); c.stroke();
      c.fillStyle = COLORS.muted; c.fillText(this.formatPrice(d.rows[index]), p.left - 8, y);
    }
    this.canvas.setAttribute('aria-label', `Heatmap da liquidez L2 visível. ${d.coverage.sampleCount} snapshots observados; ${d.rows.length} faixas de preço. Bid em verde e ask em âmbar. Colunas agregadas usam média dos snapshots, não volume negociado. ${displayTime.range(d.from, d.to)}. Lacunas ficam vazias. Use setas para inspecionar, Enter para fixar e Escape para liberar.`);
  }

  drawTimeAxis() {
    const c = this.context, p = this.plot, count = this.width < 430 ? 3 : 5;
    c.font = '9px "JetBrains Mono",monospace'; c.fillStyle = COLORS.muted;
    c.strokeStyle = COLORS.grid; c.lineWidth = .7; c.beginPath(); c.moveTo(p.left, p.bottom); c.lineTo(p.right, p.bottom); c.stroke();
    for (let i = 0; i < count; i++) {
      const time = this.axisFrom + i / (count - 1) * (this.axisTo - this.axisFrom);
      c.textAlign = i === 0 ? 'left' : i === count - 1 ? 'right' : 'center';
      c.fillText(clock(time, this.axisTo - this.axisFrom < 180000), this.xFor(time), this.height - 17);
    }
    c.textAlign = 'left'; c.font = '8px "JetBrains Mono",monospace'; c.fillText(displayTime.label(), 14, this.height - 17);
  }

  coordinates(event) {
    const rect = this.canvas.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * this.width / (rect.width || 1), y: (event.clientY - rect.top) * this.height / (rect.height || 1) };
  }

  selectionAt(x, y) {
    const d = this.data, p = this.plot;
    if (!d || !this.xFor || x < p.left || x > p.right || y < p.top || y > p.bottom) return null;
    const time = this.axisFrom + (x - p.left) / p.width * (this.axisTo - this.axisFrom);
    const gap = (d.coverage?.gaps || []).find(item => time > item.from && time < item.to);
    if (gap) return { key: `gap:${gap.from}:${gap.to}`, type: this.type, kind: 'gap', time, from: gap.from, to: gap.to, reason: gap.reason, gapKind: gap.kind, known: gap.known };
    if (this.type === 'cvd') {
      if (!d.points?.length) return null;
      const point = d.points.reduce((best, value) => Math.abs(value.lastTime - time) < Math.abs(best.lastTime - time) ? value : best);
      return { ...point, key: `cvd:${point.time}`, type: 'cvd', kind: 'point', time: point.lastTime, bucketTime: point.time, bucketSeconds: d.bucketSeconds, baseline: point.cumulative - point.delta, resetAt: d.coverage?.resetAt };
    }
    const column = d.columns.find(item => time >= item.time && (time < item.end || (item.time === item.end && time - item.time < 1)));
    if (!column) return { key: `empty:${Math.floor(time / 1000)}`, type: 'heatmap', kind: 'gap', time, from: time, to: time, reason: 'Sem snapshot ou coluna retida neste instante', gapKind: 'no_book_samples', known: true };
    const row = clamp(Math.floor((y - p.top) / p.height * d.rows.length), 0, d.rows.length - 1);
    return {
      key: `heatmap:${column.time}:${d.rows[row]}`, type: 'heatmap', kind: 'cell', time: column.lastTime,
      from: column.time, to: column.end, firstTime: column.firstTime, lastTime: column.lastTime,
      price: d.rows[row], priceTo: bucketPrice(priceIndex(d.rows[row], d.step) + 1, d.step),
      bid: column.bids[row], ask: column.asks[row], bidSamples: column.bidSamples[row], askSamples: column.askSamples[row],
      sampleCount: column.sampleCount, mid: column.mid, aggregation: column.aggregation,
    };
  }

  inspectPointer() {
    if (!this.pointer) return;
    this.hover = this.selectionAt(this.pointer.x, this.pointer.y);
    this.showInspection();
  }

  showInspection(notify = true) {
    this.drawOverlay();
    if (!this.hover) { this.tooltip.style.display = 'none'; if (notify) this.emitHover(null); return; }
    const h = this.hover, rows = [];
    const add = (label, value, color) => rows.push({ label, value: String(value), color });
    let title;
    if (h.kind === 'gap') {
      title = h.gapKind === 'feed_gap' ? 'Interrupção registrada' : h.type === 'cvd' ? 'Sem negócios observados' : 'Sem snapshot gravado';
      add('Intervalo', displayTime.range(h.from, h.to));
      add('Cobertura', h.type === 'cvd' && h.gapKind !== 'feed_gap' ? 'Não comprova perda de mensagens' : h.reason);
    } else if (h.type === 'cvd') {
      title = dateClock(h.time);
      add('CVD observado', this.formatQuantity(h.cumulative), h.cumulative >= 0 ? COLORS.lime : COLORS.down);
      add(`Delta / ${h.bucketSeconds}s`, this.formatQuantity(h.delta), h.delta >= 0 ? COLORS.bid : COLORS.down);
      add('Compra agressora', this.formatQuantity(h.buy), COLORS.bid); add('Venda agressora', this.formatQuantity(h.sell), COLORS.down);
      add('Negócios', h.count); add('Base do intervalo', this.formatQuantity(h.baseline));
      if (finite(h.resetAt)) add('Origem do CVD', dateClock(h.resetAt));
    } else {
      title = `${this.formatPrice(h.price)} – ${this.formatPrice(h.priceTo)}`;
      add('Horário observado', displayTime.range(h.firstTime, h.lastTime));
      add(h.aggregation === 'mean' ? 'Bid · média' : 'Bid observado', h.bid === null ? 'Profundidade não gravada' : this.formatQuantity(h.bid), COLORS.bid);
      add(h.aggregation === 'mean' ? 'Ask · média' : 'Ask observado', h.ask === null ? 'Profundidade não gravada' : this.formatQuantity(h.ask), COLORS.ask);
      add('Snapshots', h.sampleCount);
      if (h.bidSamples !== h.sampleCount || h.askSamples !== h.sampleCount) add('Amostras bid / ask', `${h.bidSamples} / ${h.askSamples}`);
      add('Preço médio bid/ask', this.formatPrice(h.mid));
    }
    this.tooltip.replaceChildren();
    const heading = document.createElement('div'); heading.textContent = title;
    heading.style.cssText = 'font-weight:600;margin-bottom:7px;padding-bottom:6px;border-bottom:1px solid #354151;color:#edf3f8;';
    this.tooltip.appendChild(heading);
    for (const row of rows) {
      const line = document.createElement('div'); line.style.cssText = 'display:flex;justify-content:space-between;gap:18px;margin:3px 0;';
      const label = document.createElement('span'); label.textContent = row.label; label.style.color = COLORS.muted;
      const value = document.createElement('span'); value.textContent = row.value; value.style.cssText = `text-align:right;font-family:"JetBrains Mono",monospace;color:${row.color || COLORS.text};`;
      line.append(label, value); this.tooltip.appendChild(line);
    }
    const outside = !this.xFor || h.time < this.axisFrom || h.time > this.axisTo;
    const hint = document.createElement('div'); hint.textContent = this.pinned ? `${outside ? 'Fixado · fora da janela' : 'Fixado'} · clique no mesmo ponto ou Esc para liberar` : 'Clique para fixar · setas para inspecionar';
    hint.style.cssText = 'font-size:9px;color:#758392;margin-top:8px;'; this.tooltip.appendChild(hint);
    this.tooltip.style.display = 'block';
    const x = this.pointer?.x ?? (this.xFor ? this.xFor(h.time) : this.width / 2), y = this.pointer?.y ?? this.height / 2;
    const width = this.tooltip.offsetWidth, height = this.tooltip.offsetHeight;
    this.tooltip.style.left = `${clamp(x + 15 + width > this.width ? x - width - 15 : x + 15, 4, Math.max(4, this.width - width - 4))}px`;
    this.tooltip.style.top = `${clamp(y - height / 2, 5, Math.max(5, this.height - height - 5))}px`;
    if (notify) this.emitHover({ ...h, pinned: this.pinned });
  }

  drawOverlay() {
    const c = this.overlayContext;
    if (!c) return;
    c.clearRect(0, 0, this.width, this.height);
    const h = this.hover, p = this.plot;
    if (!h || !this.xFor || !this.yFor) return;
    const x = this.xFor(h.time);
    if (x < p.left || x > p.right) return;
    const y = h.type === 'heatmap' && finite(h.price) ? this.yFor((h.price + h.priceTo) / 2) : h.type === 'cvd' && finite(h.cumulative) ? this.yFor(h.cumulative) : null;
    c.strokeStyle = this.pinned ? '#b9f58ba6' : '#aab9c480'; c.lineWidth = 1; c.setLineDash([3, 4]);
    c.beginPath(); c.moveTo(x, p.top); c.lineTo(x, p.bottom); c.stroke();
    if (finite(y) && y >= p.top && y <= p.bottom) { c.beginPath(); c.moveTo(p.left, y); c.lineTo(p.right, y); c.stroke(); }
    c.setLineDash([]);
    if (h.type === 'cvd' && finite(y) && y >= p.top && y <= p.bottom) { c.beginPath(); c.arc(x, y, 3.5, 0, Math.PI * 2); c.fillStyle = COLORS.lime; c.fill(); c.strokeStyle = COLORS.background; c.lineWidth = 2; c.stroke(); }
  }

  onKey(event) {
    if (event.key === 'Escape') { event.preventDefault(); this.clearInspection(true); return; }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (!this.hover) { this.selectKeyboard(0, 0); if (this.hover) { this.pinned = true; this.showInspection(); } }
      else { this.pinned = !this.pinned; this.showInspection(); }
      return;
    }
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    this.selectKeyboard(event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0, event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0);
  }

  selectKeyboard(dx, dy) {
    const d = this.data;
    const list = this.type === 'cvd' ? d?.points : d?.columns;
    if (!list?.length || !this.xFor) return;
    const selected = this.hover?.time;
    let index = selected == null ? list.length - 1 : list.reduce((best, item, i) => Math.abs((item.lastTime ?? item.time) - selected) < Math.abs((list[best].lastTime ?? list[best].time) - selected) ? i : best, 0);
    index = clamp(index + dx, 0, list.length - 1);
    const item = list[index];
    let y = this.type === 'cvd' ? this.yFor(item.cumulative) : this.plot.top + this.plot.height / 2;
    if (this.type === 'heatmap') {
      let row = finite(this.hover?.price) ? d.rows.indexOf(this.hover.price) : Math.floor(d.rows.length / 2);
      row = clamp((row < 0 ? Math.floor(d.rows.length / 2) : row) + dy, 0, d.rows.length - 1);
      y = this.plot.top + (row + .5) / d.rows.length * this.plot.height;
    }
    const time = this.type === 'cvd' ? item.lastTime : (Math.max(d.from, item.time) + item.end) / 2;
    this.pointer = { x: clamp(this.xFor(time), this.plot.left, this.plot.right), y };
    this.hover = this.selectionAt(this.pointer.x, y);
    this.showInspection();
  }

  emitHover(value) {
    if (typeof this.onHover !== 'function') return;
    try { this.onHover(value); } catch (error) { console.error('QHOS flow hover callback:', error); }
  }

  clearInspection(resetPin = false) {
    if (resetPin) this.pinned = false;
    this.hover = null; this.pointer = null;
    if (this.tooltip) this.tooltip.style.display = 'none';
    this.drawOverlay(); this.emitHover(null);
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.observer?.disconnect();
    globalThis.removeEventListener('resize', this.windowResize);
    for (const [name, handler] of Object.entries(this.handlers)) this.canvas.removeEventListener(name, handler);
    this.canvas.remove(); this.overlay.remove(); this.tooltip.remove();
    this.container.style.position = this.originalPosition;
    this.container.style.minHeight = this.originalMinHeight;
    this.emitHover(null);
  }
}
