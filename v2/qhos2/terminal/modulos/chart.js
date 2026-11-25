import { ema, rsi, formatPrice, displayTime } from 'qhos-module/market.js';
import { executionIndexAtTime, executionTimeAtIndex } from 'qhos-module/executions.js';

// Canvas rendering and indicators use the actual OHLCV supplied by the feed layer.
const DARK = { bg: '#101317', grid: '#1c2229', text: '#697584', strong: '#c8d0dc', up: '#24c9a0', down: '#ec6573', cross: '#66768b', tag: '#2b3440', border: '#222932', watermark: 'rgba(153,173,195,.035)' };
const LIGHT = { bg: '#f8fafc', grid: '#e5eaf0', text: '#7a8595', strong: '#303d4f', up: '#0eac86', down: '#dd5866', cross: '#8290a2', tag: '#dce4ed', border: '#dde4ec', watermark: 'rgba(40,60,85,.035)' };
const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
const finite = n => typeof n === 'number' && Number.isFinite(n);

export class TerminalChart {
  constructor(container, callbacks = {}) {
    this.container = container;
    this.callbacks = callbacks;
    this.options = { type: 'candles', ema20: true, ema50: true, volume: true, rsi: true, theme: 'dark', grid: true };
    this.data = [];
    this.asset = null;
    this.drawings = [];
    this.markers = [];
    this.executions = [];
    this.executionGlyphs = [];
    this.executionInterval = 60;
    this.executionUntil = Infinity;
    this.selectedExecutionId = null;
    this.hoveredExecutionKey = null;
    this.pendingPoint = null;
    this.tool = 'crosshair';
    this.hover = null;
    this.hoverIndex = null;
    this.keyboardMode = false;
    this.keyboardTime = null;
    this.drag = null;
    this.view = { start: 0, span: 142 };
    this.futureBars = 13;
    this.autoFit = true;
    this.executionFocus = false;
    this.lastViewStateKey = null;
    this.frame = 0;
    this.destroyed = false;
    // Preserve the host's absolute inset layout; only static containers need a
    // positioning context for the two canvas layers.
    if (window.getComputedStyle(this.container).position === 'static') this.container.style.position = 'relative';
    this.container.style.overflow = 'hidden';
    this.canvas = document.createElement('canvas');
    this.overlay = document.createElement('canvas');
    for (const canvas of [this.canvas, this.overlay]) {
      Object.assign(canvas.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', display: 'block' });
      this.container.appendChild(canvas);
    }
    this.canvas.setAttribute('aria-hidden', 'true');
    this.overlay.style.touchAction = 'pan-y';
    this.overlay.style.cursor = 'crosshair';
    this.overlay.setAttribute('role', 'img');
    this.overlay.tabIndex = 0;
    this.overlay.setAttribute('aria-keyshortcuts', 'ArrowLeft ArrowRight E Enter [ ]');
    this.overlay.setAttribute('aria-label', 'Gráfico de preços do ativo selecionado. Arraste para navegar, role para ampliar. Com o gráfico focado, use as setas para candles, E para a última execução e colchetes para navegar entre execuções.');
    this.ctx = this.canvas.getContext('2d');
    this.over = this.overlay.getContext('2d');
    this.handlers = {
      move: e => this.pointerMove(e),
      down: e => this.pointerDown(e),
      up: e => this.pointerUp(e),
      leave: () => this.pointerLeave(),
      cancel: () => { this.drag = null; this.pointerLeave(); },
      wheel: e => {
        e.preventDefault();
        if (!this.data.length) return;
        const rect = this.overlay.getBoundingClientRect();
        this.zoom(Math.exp(-clamp(e.deltaY, -140, 140) * .0025), e.clientX - rect.left);
      },
      dblclick: () => { if (this.tool === 'crosshair') this.resetView(); },
      contextmenu: e => this.contextMenu(e),
      keydown: e => this.keyDown(e),
      blur: () => { this.keyboardMode = false; this.keyboardTime = null; this.pointerLeave(); }
    };
    this.overlay.addEventListener('pointermove', this.handlers.move);
    this.overlay.addEventListener('pointerdown', this.handlers.down);
    this.overlay.addEventListener('pointerup', this.handlers.up);
    this.overlay.addEventListener('pointercancel', this.handlers.cancel);
    this.overlay.addEventListener('pointerleave', this.handlers.leave);
    this.overlay.addEventListener('wheel', this.handlers.wheel, { passive: false });
    this.overlay.addEventListener('dblclick', this.handlers.dblclick);
    this.overlay.addEventListener('contextmenu', this.handlers.contextmenu);
    this.overlay.addEventListener('keydown', this.handlers.keydown);
    this.overlay.addEventListener('blur', this.handlers.blur);
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.container);
    this.resize();
  }

  setData(candles, asset) {
    const previousLength = this.data.length;
    const anchorTime = previousLength ? executionTimeAtIndex(this.data, this.view.start - .5, this.executionInterval) : null;
    const selectedAnchor = this.executions.find(event => event.id === this.selectedExecutionId);
    const selectedIndex = selectedAnchor ? this.executionIndex(selectedAnchor) : null;
    const selectedRatio = selectedIndex === null ? null : (selectedIndex - this.view.start + .5) / this.view.span;
    const symbolChanged = !!this.asset && this.asset.symbol !== asset.symbol;
    // Timeframe changes are explicit in setExecutions; the distance between
    // observed candles is not a reliable interval when a bucket is absent.
    const changed = !this.asset || symbolChanged;
    const followsLatest = !this.selectedExecutionId && this.view.start + this.view.span >= previousLength + 2;
    this.data = candles;
    this.asset = asset;
    this.ema20 = ema(candles, 20);
    this.ema50 = ema(candles, 50);
    this.rsi14 = rsi(candles, 14);
    this.overlay.setAttribute('aria-label', `Gráfico de ${asset.name || asset.displaySymbol || asset.symbol}, fonte ${asset.sourceLabel || (asset.provider === 'rithmic' ? 'Rithmic' : 'Coinbase Exchange')}. Arraste para navegar e role para ampliar. Com o gráfico focado, use as setas para candles, E para a última execução e colchetes para navegar entre execuções.`);
    if (changed || !previousLength || !candles.length) {
      if (symbolChanged) { this.drawings = []; this.markers = []; this.executions = []; this.executionGlyphs = []; this.selectedExecutionId = null; this.notifyExecutionHover(null); }
      this.pendingPoint = null;
      this.resetView();
    } else {
      if (this.autoFit) this.fitCandles();
      else if (followsLatest && previousLength !== candles.length) this.view.start += candles.length - previousLength;
      else if (!followsLatest) {
        // A newly observed candle can collapse right-hand time padding into one
        // recorded bucket. Preserve time (or the selected event's screen ratio),
        // never its old synthetic logical index through that coverage change.
        const selected = selectedAnchor ? this.executionIndex(selectedAnchor) : null;
        const anchor = finite(anchorTime) ? executionIndexAtTime(candles, anchorTime, this.executionInterval) : null;
        if (selected !== null && finite(selectedRatio)) this.view.start = selected + .5 - this.view.span * selectedRatio;
        else if (anchor !== null) this.view.start = anchor + .5;
      }
      this.constrainView();
      this.schedule();
    }
    return this;
  }

  setOptions(options) {
    this.options = { ...this.options, ...options };
    this.schedule();
    return this;
  }

  setTool(tool) {
    this.tool = ['crosshair', 'trend', 'horizontal'].includes(tool) ? tool : 'crosshair';
    this.pendingPoint = null;
    this.overlay.style.cursor = 'crosshair';
    this.schedule();
    return this;
  }

  setMarkers(markers) {
    this.markers = (Array.isArray(markers) ? markers : []).filter(marker => marker && finite(marker.price) && marker.price > 0)
      .slice(0, 6).map(marker => ({ price: marker.price, label: String(marker.label ?? '').replace(/[\r\n\t]/g, ' ').slice(0, 64),
        color: typeof marker.color === 'string' && marker.color.length <= 80 ? marker.color : '#95a7bc' }));
    this.schedule();
    return this;
  }

  setExecutions(events, { intervalSeconds = this.executionInterval, until = Infinity } = {}) {
    const intervalChanged = finite(intervalSeconds) && intervalSeconds > 0 && intervalSeconds !== this.executionInterval;
    if (finite(intervalSeconds) && intervalSeconds > 0) this.executionInterval = intervalSeconds;
    if (intervalChanged && this.data.length) this.resetView();
    this.executionUntil = finite(until) ? until : Infinity;
    const symbol = this.asset?.symbol;
    this.executions = (Array.isArray(events) ? events : []).filter(event => event
      && typeof event.id === 'string' && finite(event.time) && event.time > 0 && event.time <= this.executionUntil
      && finite(event.price) && event.price > 0 && finite(event.size) && event.size > 0
      && ['buy', 'sell'].includes(event.side) && ['market', 'paper'].includes(event.kind)
      && (!symbol || event.symbol === symbol)).map(event => ({ ...event }))
      .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
    if (this.selectedExecutionId && !this.executions.some(event => event.id === this.selectedExecutionId)) this.selectedExecutionId = null;
    this.schedule();
    return this;
  }

  executionIndex(event) {
    const index = executionIndexAtTime(this.data, event.time, this.executionInterval);
    if (index !== null || event.kind !== 'paper' || !this.data.length || event.time > this.executionUntil) return index;
    const last = this.data.length - 1, start = this.data[last].time * 1000, duration = this.executionInterval * 1000;
    // A simulated fill can precede the first public trade of a new bucket.
    // Plot its local timestamp in the empty right-hand time padding; no candle
    // or exchange execution is invented to support the mark.
    return event.time >= start + duration ? last - .5 + (event.time - start) / duration : null;
  }

  executionExtent() {
    let extent = this.data.length + this.futureBars;
    for (const event of this.executions) if (event.kind === 'paper') {
      const index = this.executionIndex(event);
      if (index !== null) extent = Math.max(extent, index + 1.5);
    }
    return extent;
  }

  minimumSpan() {
    // A 100 ms visible window allows neighboring millisecond prints to separate.
    // It does not manufacture a finer exchange timestamp than the feed recorded.
    return .1 / this.executionInterval;
  }

  focusExecution(id, { windowSeconds = 10 } = {}) {
    const event = this.executions.find(item => item.id === id);
    if (!event || !this.data.length) return false;
    const index = this.executionIndex(event);
    if (index === null) return false;
    const seconds = finite(windowSeconds) && windowSeconds > 0 ? windowSeconds : 10;
    this.autoFit = false;
    this.executionFocus = true;
    this.view.span = Math.max(this.minimumSpan(), seconds / this.executionInterval);
    this.view.start = index + .5 - this.view.span / 2;
    this.selectedExecutionId = event.id;
    this.keyboardMode = false; this.keyboardTime = null;
    this.hover = null; this.hoverIndex = null;
    this.constrainView(); this.setupLayout(); this.schedule();
    this.callbacks.onCrosshair?.(null);
    return true;
  }

  selectExecution(id) {
    if (!this.executions.some(event => event.id === id)) return false;
    this.selectedExecutionId = id;
    this.schedule();
    return true;
  }

  clearExecutionSelection() {
    this.selectedExecutionId = null;
    this.schedule();
  }

  executionPayload(glyph) {
    return glyph ? { events: glyph.events.map(event => ({ ...event })), x: glyph.x, y: glyph.y, grouped: glyph.events.length > 1 } : null;
  }

  notifyExecutionHover(glyph) {
    const key = glyph ? `${glyph.events[0].id}:${glyph.events.at(-1).id}:${glyph.events.length}` : null;
    if (this.hoveredExecutionKey === key) return;
    this.hoveredExecutionKey = key;
    this.callbacks.onExecutionHover?.(this.executionPayload(glyph));
  }

  hitExecution(point) {
    if (!point || !this.layout || this.tool !== 'crosshair') return null;
    let match = null, distance = Infinity;
    for (const glyph of this.executionGlyphs) {
      const d = Math.hypot(glyph.x - point.x, glyph.y - point.y);
      if (d <= glyph.radius + 5 && (d < distance || d === distance && glyph.kind === 'paper')) { match = glyph; distance = d; }
    }
    return match;
  }

  selectExecutionGlyph(glyph) {
    if (!glyph) return;
    this.selectedExecutionId = glyph.events.at(-1).id;
    this.callbacks.onExecutionSelect?.(this.executionPayload(glyph));
    this.schedule();
  }

  getDrawings() {
    return this.drawings.map(drawing => drawing.type === 'horizontal'
      ? { type: 'horizontal', a: { time: drawing.a.time, price: drawing.a.price } }
      : { type: 'trend', a: { time: drawing.a.time, price: drawing.a.price }, b: { time: drawing.b.time, price: drawing.b.price } });
  }

  setDrawings(drawings) {
    // Drawing times are UNIX seconds, matching candle.time; millisecond values
    // are intentionally rejected instead of silently changing their meaning.
    const validPoint = point => point && finite(point.time) && point.time > 0 && point.time < 1e11 && finite(point.price) && point.price > 0;
    this.drawings = (Array.isArray(drawings) ? drawings : []).filter(drawing => drawing
      && ['horizontal', 'trend'].includes(drawing.type) && validPoint(drawing.a)
      && (drawing.type !== 'trend' || validPoint(drawing.b))).slice(-100)
      .map(drawing => drawing.type === 'horizontal'
        ? { type: 'horizontal', a: { time: drawing.a.time, price: drawing.a.price } }
        : { type: 'trend', a: { time: drawing.a.time, price: drawing.a.price }, b: { time: drawing.b.time, price: drawing.b.price } });
    this.pendingPoint = null;
    this.schedule();
    return this;
  }

  clearDrawings() {
    this.drawings = [];
    this.pendingPoint = null;
    this.callbacks.onDraw?.(this.getDrawings());
    this.schedule();
  }

  fitCandles() {
    const count = this.data.length;
    const plotW = Math.max(10, (this.width || 900) - 10 - (this.width < 520 ? 65 : 77));
    // Sparse history stays centered with readable bodies, without reserving
    // thirteen empty future buckets or stretching two bars across the screen.
    const padding = Math.min(this.futureBars, Math.max(2, count * .12));
    this.view.span = Math.min(142, Math.max(6, plotW / 90, count + padding));
    this.view.start = count + padding <= 142 ? (count - this.view.span) / 2 : count + this.futureBars - this.view.span;
  }

  getViewState(layout = null) {
    const count = this.data.length;
    const l = layout || (count && this.width && this.height ? this.setupLayout() : null);
    const first = Math.max(0, Math.ceil(this.view.start - .5));
    const last = Math.min(count - 1, Math.floor(this.view.start + this.view.span - .5));
    let gapCount = 0;
    for (let i = 1; i < count; i++) if (this.gapBefore(i)) gapCount++;
    return {
      mode: this.executionFocus || this.view.span < 3 ? 'executions' : 'candles',
      detailed: !!l?.detailed,
      totalCandles: count,
      visibleCandles: Math.max(0, last - first + 1),
      from: count ? this.data[0].time * 1000 : null,
      to: count ? this.data[count - 1].time * 1000 : null,
      intervalSeconds: this.executionInterval,
      gapCount,
      windowSeconds: this.view.span * this.executionInterval
    };
  }

  notifyViewMode(layout = null) {
    const state = this.getViewState(layout), key = JSON.stringify(state);
    if (key === this.lastViewStateKey) return;
    this.lastViewStateKey = key;
    this.callbacks.onViewMode?.(state);
  }

  resetView() {
    this.autoFit = true;
    this.executionFocus = false;
    this.fitCandles();
    this.hover = null;
    this.hoverIndex = null;
    this.keyboardMode = false;
    this.keyboardTime = null;
    this.selectedExecutionId = null;
    this.notifyExecutionHover(null);
    this.callbacks.onCrosshair?.(null);
    this.schedule();
  }

  constrainView() {
    const extent = this.executionExtent();
    const maxSpan = Math.max(40, extent + 4);
    this.view.span = clamp(this.view.span, this.minimumSpan(), maxSpan);
    const leftPadding = Math.min(-3, (this.data.length - this.view.span) / 2);
    this.view.start = clamp(this.view.start, leftPadding, Math.max(leftPadding, extent - this.view.span));
  }

  zoom(factor, atX) {
    if (!this.layout || !this.data.length || !(factor > 0)) return;
    const { left, plotW } = this.layout;
    const ratio = finite(atX) ? clamp((atX - left) / plotW, 0, 1) : .7;
    const anchor = this.view.start + this.view.span * ratio;
    this.autoFit = false;
    this.view.span /= factor;
    this.view.span = clamp(this.view.span, this.minimumSpan(), Math.max(40, this.executionExtent() + 4));
    this.view.start = anchor - this.view.span * ratio;
    if (this.view.span >= 3) this.executionFocus = false;
    this.constrainView();
    this.schedule();
  }

  resize() {
    if (this.destroyed) return;
    const rect = this.container.getBoundingClientRect();
    this.width = Math.max(1, Math.round(rect.width));
    this.height = Math.max(1, Math.round(rect.height));
    this.dpr = Math.min(window.devicePixelRatio || 1, 3);
    for (const canvas of [this.canvas, this.overlay]) {
      canvas.width = Math.round(this.width * this.dpr);
      canvas.height = Math.round(this.height * this.dpr);
    }
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.over.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    if (this.autoFit) this.fitCandles();
    this.schedule();
  }

  schedule() {
    if (this.destroyed || this.frame) return;
    this.frame = requestAnimationFrame(() => { this.frame = 0; this.render(); });
  }

  priceText(value) {
    try { return formatPrice(value, this.asset); } catch { return value.toLocaleString('pt-BR', { maximumFractionDigits: 2 }); }
  }

  setupLayout() {
    const w = this.width, h = this.height;
    const left = 10, right = w < 520 ? 65 : 77, top = Math.min(86, h * .2);
    const plotW = Math.max(10, w - left - right);
    const bottom = h - 28;
    const showRsi = this.options.rsi && h >= 240 && this.view.span >= 3;
    const rsiHeight = showRsi ? Math.max(80, Math.min(118, (bottom - top) * .24)) : 0;
    const rsiTop = showRsi ? bottom - rsiHeight : null;
    const priceBottom = showRsi ? rsiTop - 9 : bottom - 2;
    const first = Math.max(0, Math.floor(this.view.start));
    const last = Math.min(this.data.length - 1, Math.ceil(this.view.start + this.view.span));
    const visible = this.data.slice(first, last + 1);
    const logicalFrom = this.view.start - .5, logicalTo = logicalFrom + this.view.span;
    const executionPoints = [];
    for (const event of this.executions) {
      if (event.time > this.executionUntil || event.symbol !== this.asset?.symbol) continue;
      const index = this.executionIndex(event);
      if (index !== null && index >= logicalFrom && index <= logicalTo) executionPoints.push({ event, index });
    }
    const detailed = this.view.span < 3 && executionPoints.length > 0;
    let min = Infinity, max = -Infinity;
    if (!detailed) {
      for (let i = first; i <= last; i++) {
        const bar = this.data[i];
        min = Math.min(min, bar.low); max = Math.max(max, bar.high);
        if (this.options.ema20 && finite(this.ema20[i])) { min = Math.min(min, this.ema20[i]); max = Math.max(max, this.ema20[i]); }
        if (this.options.ema50 && finite(this.ema50[i])) { min = Math.min(min, this.ema50[i]); max = Math.max(max, this.ema50[i]); }
      }
    }
    // Paper fills and recorded trades remain on scale even if they differ from
    // the candle close; intrabar inspection scales to the observed executions.
    for (const { event } of executionPoints) { min = Math.min(min, event.price); max = Math.max(max, event.price); }
    if (!finite(min) || !finite(max)) { min = 0; max = 1; }
    const span = Math.max(max - min, Math.abs(max) * (detailed ? .00002 : .001), .0001);
    min -= span * (this.options.volume && !detailed ? .26 : .16);
    max += span * .16;
    const priceH = Math.max(20, priceBottom - top);
    const stepX = plotW / this.view.span;
    const x = i => left + (i - this.view.start + .5) * stepX;
    const y = v => top + (max - v) / (max - min) * priceH;
    const priceAtY = py => max - (py - top) / priceH * (max - min);
    this.layout = { w, h, left, right, top, bottom, plotW, priceBottom, priceH, first, last, min, max, stepX, x, y, priceAtY, visible, showRsi, rsiTop, rsiHeight, executionPoints, detailed };
    return this.layout;
  }

  render() {
    if (this.destroyed || !this.ctx || this.width < 10 || this.height < 10) return;
    const ctx = this.ctx;
    const p = this.options.theme === 'light' ? LIGHT : DARK;
    this.palette = p;
    ctx.clearRect(0, 0, this.width, this.height);
    ctx.fillStyle = p.bg; ctx.fillRect(0, 0, this.width, this.height);
    if (!this.data.length) { this.executionGlyphs = []; this.notifyExecutionHover(null); this.notifyViewMode(); this.over.clearRect(0, 0, this.width, this.height); return; }
    const l = this.setupLayout();
    const { w, h, left, right, top, bottom, plotW, priceBottom, first, last, x, y, stepX } = l;
    ctx.font = '10px "JetBrains Mono", "SFMono-Regular", Consolas, monospace';
    ctx.textBaseline = 'middle';
    ctx.lineWidth = 1;
    const tickCount = Math.max(3, Math.round(l.priceH / 64));
    const rawStep = (l.max - l.min) / tickCount;
    const power = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const normalized = rawStep / power;
    const tickStep = (normalized < 1.5 ? 1 : normalized < 3 ? 2 : normalized < 7 ? 5 : 10) * power;
    const firstTick = Math.ceil(l.min / tickStep) * tickStep;
    for (let value = firstTick; value < l.max; value += tickStep) {
      const yy = Math.round(y(value)) + .5;
      if (this.options.grid) { ctx.strokeStyle = p.grid; ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(w - right, yy); ctx.stroke(); }
      ctx.fillStyle = p.text; ctx.fillText(this.priceText(value), w - right + 10, yy);
    }
    this.drawTimeAxis(ctx, l);
    ctx.strokeStyle = p.border;
    ctx.beginPath(); ctx.moveTo(w - right + .5, 0); ctx.lineTo(w - right + .5, h); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, bottom + .5); ctx.lineTo(w, bottom + .5); ctx.stroke();
    ctx.save();
    ctx.beginPath(); ctx.rect(left, top - 15, plotW, priceBottom - top + 15); ctx.clip();
    ctx.fillStyle = p.watermark;
    ctx.font = `600 ${Math.min(95, plotW * .15)}px Arial, sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillText('QHOS', left + plotW * .52, top + l.priceH * .48);
    ctx.textAlign = 'left';
    if (this.options.volume && !l.detailed) {
      const maxVolume = Math.max(1, ...l.visible.map(b => b.volume));
      const volumeHeight = Math.min(70, l.priceH * .21);
      for (let i = first; i <= last; i++) {
        const b = this.data[i];
        const height = (b.volume / maxVolume) * volumeHeight;
        ctx.fillStyle = b.close >= b.open ? 'rgba(36,201,160,.21)' : 'rgba(236,101,115,.20)';
        const volumeWidth = Math.max(1, Math.min(30, stepX * .65));
        ctx.fillRect(x(i) - volumeWidth / 2, priceBottom - height, volumeWidth, height);
      }
      ctx.fillStyle = p.text; ctx.font = '9px "JetBrains Mono", Consolas, monospace';
      ctx.fillText('VOL', left + 4, priceBottom - 9);
    }
    if (this.options.type === 'candles') {
      const bodyWidth = Math.max(1.3, Math.min(24, stepX * .62));
      for (let i = first; i <= last; i++) {
        const b = this.data[i];
        const xx = Math.round(x(i)) + .5;
        const color = b.close >= b.open ? p.up : p.down;
        ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(xx, y(b.high)); ctx.lineTo(xx, y(b.low)); ctx.stroke();
        const yy = Math.min(y(b.open), y(b.close));
        const bodyHeight = Math.max(1.3, Math.abs(y(b.open) - y(b.close)));
        ctx.fillRect(xx - bodyWidth / 2, yy, bodyWidth, bodyHeight);
      }
    } else {
      if (this.options.type === 'area') {
        const gradient = ctx.createLinearGradient(0, top, 0, priceBottom);
        gradient.addColorStop(0, 'rgba(36,201,160,.22)'); gradient.addColorStop(1, 'rgba(36,201,160,.015)');
        let segmentStart = first;
        for (let end = first; end <= last; end++) {
          if (end < last && !this.gapBefore(end + 1)) continue;
          ctx.beginPath();
          for (let i = segmentStart; i <= end; i++) { if (i === segmentStart) ctx.moveTo(x(i), y(this.data[i].close)); else ctx.lineTo(x(i), y(this.data[i].close)); }
          ctx.lineTo(x(end), priceBottom); ctx.lineTo(x(segmentStart), priceBottom); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
          segmentStart = end + 1;
        }
      }
      this.line(ctx, this.data.map(b => b.close), p.up, 1.8, x, y, first, last);
    }
    if (this.options.ema50 && !l.detailed) this.line(ctx, this.ema50, '#5d86dc', 1.35, x, y, first, last);
    if (this.options.ema20 && !l.detailed) this.line(ctx, this.ema20, '#dba754', 1.35, x, y, first, last);
    this.drawGaps(ctx, l);
    this.drawSaved(ctx, l);
    this.drawMarkers(ctx, l);
    this.drawExecutions(ctx, l);
    ctx.restore();
    const latest = this.data[this.data.length - 1];
    const priceY = y(latest.close);
    if (priceY >= top && priceY <= priceBottom) {
      const lastColor = latest.close >= latest.open ? p.up : p.down;
      ctx.strokeStyle = lastColor; ctx.globalAlpha = .65; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(left, priceY + .5); ctx.lineTo(w - right, priceY + .5); ctx.stroke();
      ctx.globalAlpha = 1; ctx.setLineDash([]);
      ctx.fillStyle = lastColor; ctx.fillRect(w - right + 1, priceY - 12, right - 1, 24);
      ctx.fillStyle = '#061b16'; ctx.font = '600 10px "JetBrains Mono", Consolas, monospace';
      ctx.fillText(this.priceText(latest.close), w - right + 8, priceY + .5);
      const latestX = x(this.data.length - 1);
      if (this.options.type !== 'candles' && latestX >= left && latestX < w - right) {
        ctx.fillStyle = lastColor; ctx.beginPath(); ctx.arc(latestX, priceY, 3, 0, Math.PI * 2); ctx.fill();
      }
    }
    if (l.showRsi) this.drawRsi(ctx, l);
    if (this.keyboardMode) this.syncKeyboardHover();
    this.renderOverlay();
    if (!this.drag?.active) this.notifyExecutionHover(this.hitExecution(this.hover));
    this.notifyViewMode(l);
  }

  refreshTimeZone() {
    // Only repaint labels and the existing crosshair; candle times and view stay intact.
    this.schedule();
  }

  timeAxisLabel(time, { precise = false, milliseconds = false, dateOnly = false } = {}) {
    const formatter = this.exportTimeDisplay ?? displayTime;
    const label = precise ? formatter.time(time, { milliseconds })
      : dateOnly ? formatter.date(time) : formatter.time(time, { seconds: false });
    return label === '—' ? '' : label;
  }

  drawTimeAxis(ctx, l) {
    const p = this.palette, duration = this.view.span * this.executionInterval;
    ctx.font = '10px "JetBrains Mono", Consolas, monospace';
    ctx.textAlign = 'center';
    if (l.detailed || duration < 300) {
      // The explicit bucket duration is essential when neighboring candles have
      // missing intervals: using their distance would stretch executions.
      const slots = Math.max(2, Math.floor(l.plotW / 130));
      for (let tick = 0; tick <= slots; tick++) {
        const ratio = (tick + .5) / (slots + 1), logical = this.view.start - .5 + ratio * this.view.span;
        const time = executionTimeAtIndex(this.data, logical, this.executionInterval);
        if (!finite(time)) continue;
        const xx = l.left + ratio * l.plotW;
        if (this.options.grid) { ctx.strokeStyle = p.grid; ctx.beginPath(); ctx.moveTo(xx, l.top - 20); ctx.lineTo(xx, l.bottom); ctx.stroke(); }
        ctx.fillStyle = p.text; ctx.fillText(this.timeAxisLabel(time, { precise: true, milliseconds: duration < 20 }), xx, l.h - 12);
      }
    } else {
      const every = l.stepX >= 60 ? 1 : Math.max(1, Math.round(this.view.span / Math.max(3, Math.floor(l.plotW / 122))));
      for (let i = Math.ceil(Math.max(0, this.view.start) / every) * every; i <= l.last; i += every) {
        const xx = Math.round(l.x(i)) + .5;
        if (xx < l.left + 15 || xx > l.w - l.right - 15) continue;
        if (this.options.grid) { ctx.strokeStyle = p.grid; ctx.beginPath(); ctx.moveTo(xx, l.top - 20); ctx.lineTo(xx, l.bottom); ctx.stroke(); }
        ctx.fillStyle = p.text; ctx.fillText(this.timeAxisLabel(this.data[i].time * 1000, { dateOnly: this.executionInterval >= 86400 || every * this.executionInterval >= 86400 }), xx, l.h - 12);
      }
    }
    ctx.textAlign = 'left';
    ctx.fillStyle = p.text;
    ctx.fillText((this.exportTimeDisplay ?? displayTime).label(), l.w - l.right + 8, l.h - 12);
  }

  drawExecutions(ctx, l) {
    const bins = new Map(), cell = 9;
    for (const { event, index } of l.executionPoints) {
      const x = l.x(index), y = l.y(event.price);
      if (x < l.left || x > l.w - l.right || y < l.top || y > l.priceBottom) continue;
      const key = `${event.kind}:${event.side}:${Math.floor(x / cell)}:${Math.floor(y / cell)}`;
      let glyph = bins.get(key);
      if (!glyph) { glyph = { x: 0, y: 0, volume: 0, kind: event.kind, side: event.side, events: [], radius: 3 }; bins.set(key, glyph); }
      glyph.x += x; glyph.y += y; glyph.volume += event.size; glyph.events.push(event);
    }
    this.executionGlyphs = [...bins.values()];
    const largest = Math.max(1e-12, ...this.executionGlyphs.map(glyph => glyph.volume));
    for (const glyph of this.executionGlyphs) {
      glyph.x /= glyph.events.length; glyph.y /= glyph.events.length;
      glyph.radius = glyph.kind === 'paper' ? 7 : 3 + Math.min(5, Math.sqrt(glyph.volume / largest) * 5);
      if (glyph.events.length > 1) glyph.radius = Math.max(8, glyph.radius);
    }
    this.executionGlyphs.sort((a, b) => (a.kind === 'paper') - (b.kind === 'paper'));
    ctx.save(); ctx.setLineDash([]); ctx.font = '600 8px "JetBrains Mono", Consolas, monospace'; ctx.textAlign = 'center';
    for (const glyph of this.executionGlyphs) {
      const color = glyph.side === 'buy' ? this.palette.up : this.palette.down;
      const selected = glyph.events.some(event => event.id === this.selectedExecutionId);
      const { x, y, radius: r } = glyph;
      ctx.globalAlpha = 1; ctx.lineWidth = glyph.kind === 'paper' ? 2 : 1;
      ctx.fillStyle = glyph.kind === 'paper' ? this.palette.bg : color;
      ctx.strokeStyle = glyph.kind === 'paper' ? '#eaf4ff' : color;
      ctx.beginPath();
      if (glyph.kind === 'paper') {
        const direction = glyph.side === 'buy' ? -1 : 1;
        ctx.moveTo(x, y + direction * r); ctx.lineTo(x - r, y - direction * r * .7); ctx.lineTo(x + r, y - direction * r * .7); ctx.closePath();
      } else ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.globalAlpha = glyph.events.length > 1 || glyph.kind === 'paper' ? .95 : .74; ctx.fill(); ctx.globalAlpha = 1; ctx.stroke();
      if (glyph.kind === 'paper') { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fill(); }
      if (glyph.events.length > 1) {
        ctx.fillStyle = glyph.kind === 'paper' ? this.palette.strong : '#061a16';
        const count = glyph.events.length > 99 ? '99+' : String(glyph.events.length);
        ctx.fillText(count, x, y + .5);
      }
      if (selected) { ctx.strokeStyle = '#edf7ff'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, r + 4, 0, Math.PI * 2); ctx.stroke(); }
    }
    ctx.restore();
  }

  gapBefore(index) {
    if (index < 1 || index >= this.data.length) return 0;
    const seconds = this.data[index].time - this.data[index - 1].time - this.executionInterval;
    return finite(seconds) && seconds > .001 ? seconds : 0;
  }

  gapLabel(seconds) {
    let remaining = Math.round(seconds);
    const parts = [];
    for (const [unit, size] of [['d', 86400], ['h', 3600], ['min', 60], ['s', 1]]) {
      const value = Math.floor(remaining / size);
      if (value) parts.push(`${value} ${unit}`);
      remaining %= size;
    }
    return `Sem candles: ${parts.join(' ') || '<1 s'}`;
  }

  drawGaps(ctx, l) {
    // Observed bars use compact spacing. Mark every skipped interval explicitly;
    // its duration comes from UTC timestamps, never from fabricated gap candles.
    let labelEnd = -Infinity;
    ctx.save();
    ctx.font = '10px "JetBrains Mono", Consolas, monospace';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.strokeStyle = this.palette.text; ctx.fillStyle = this.palette.text;
    for (let i = Math.max(1, l.first); i <= l.last; i++) {
      const seconds = this.gapBefore(i);
      if (!seconds) continue;
      const xx = (l.x(i - 1) + l.x(i)) / 2;
      if (xx < l.left || xx > l.w - l.right) continue;
      ctx.setLineDash([2, 5]); ctx.globalAlpha = .7;
      ctx.beginPath(); ctx.moveTo(xx, l.top + 24); ctx.lineTo(xx, l.priceBottom - 9); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
      ctx.beginPath(); ctx.moveTo(xx - 5, l.priceBottom - 1); ctx.lineTo(xx - 1, l.priceBottom - 7);
      ctx.moveTo(xx + 1, l.priceBottom - 1); ctx.lineTo(xx + 5, l.priceBottom - 7); ctx.stroke();
      const label = this.gapLabel(seconds), width = ctx.measureText(label).width + 12;
      const labelX = clamp(xx - width / 2, l.left, l.w - l.right - width);
      if (width > l.plotW || labelX < labelEnd + 10) continue;
      ctx.fillStyle = this.palette.bg; ctx.fillRect(labelX, l.top + 3, width, 18);
      ctx.fillStyle = this.palette.text; ctx.fillText(label, labelX + width / 2, l.top + 12);
      labelEnd = labelX + width;
    }
    ctx.restore();
  }

  line(ctx, values, color, width, x, y, first, last) {
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = 'round'; ctx.beginPath();
    let started = false;
    for (let i = first; i <= last; i++) {
      if (!finite(values[i])) { started = false; continue; }
      if (!started || this.gapBefore(i)) { ctx.moveTo(x(i), y(values[i])); started = true; } else ctx.lineTo(x(i), y(values[i]));
    }
    ctx.stroke();
  }

  drawRsi(ctx, l) {
    const p = this.palette;
    const { w, right, left, plotW, bottom, rsiTop, x, first, last } = l;
    const rsiY = v => rsiTop + 27 + (100 - v) / 100 * Math.max(20, bottom - rsiTop - 32);
    l.rsiY = rsiY;
    ctx.fillStyle = p.bg; ctx.fillRect(0, rsiTop, w, bottom - rsiTop);
    ctx.strokeStyle = p.border; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, rsiTop + .5); ctx.lineTo(w, rsiTop + .5); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(w - right + .5, rsiTop); ctx.lineTo(w - right + .5, bottom); ctx.stroke();
    ctx.fillStyle = this.options.theme === 'light' ? 'rgba(139,108,218,.04)' : 'rgba(139,108,218,.055)';
    ctx.fillRect(left, rsiY(70), plotW, rsiY(30) - rsiY(70));
    ctx.font = '10px "JetBrains Mono", Consolas, monospace'; ctx.textBaseline = 'middle';
    for (const value of [30, 50, 70]) {
      const yy = rsiY(value);
      if (this.options.grid) {
        ctx.strokeStyle = value === 50 ? p.grid : (this.options.theme === 'light' ? '#d9d3e8' : '#302b3d');
        ctx.setLineDash([4, 5]); ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(w - right, yy); ctx.stroke(); ctx.setLineDash([]);
      }
      ctx.fillStyle = p.text; ctx.fillText(String(value), w - right + 10, yy);
    }
    ctx.fillStyle = p.strong; ctx.font = '10px "JetBrains Mono", Consolas, monospace';
    ctx.fillText('RSI', left + 4, rsiTop + 16);
    ctx.fillStyle = p.text; ctx.fillText('14', left + 30, rsiTop + 16);
    const finalRsi = this.rsi14[this.rsi14.length - 1];
    ctx.fillStyle = '#ad8bdc'; ctx.fillText(finite(finalRsi) ? finalRsi.toFixed(2) : '—', left + 53, rsiTop + 16);
    ctx.save(); ctx.beginPath(); ctx.rect(left, rsiTop + 24, plotW, bottom - rsiTop - 24); ctx.clip();
    this.line(ctx, this.rsi14, '#a88bd7', 1.4, x, rsiY, first, last); ctx.restore();
  }

  drawSaved(ctx, l) {
    const draws = [...this.drawings];
    if (this.pendingPoint && this.hover && this.tool === 'trend') {
      const point = this.pointAt(this.hover);
      if (point) draws.push({ type: 'trend', a: this.pendingPoint, b: point });
    }
    for (const drawing of draws) {
      const aIndex = this.indexAtTime(drawing.a.time);
      const bIndex = drawing.type === 'trend' ? this.indexAtTime(drawing.b.time) : null;
      if (drawing.a.time > this.data.at(-1).time || (drawing.type === 'trend' && (aIndex === null || bIndex === null))) continue;
      ctx.strokeStyle = '#61a2f5'; ctx.lineWidth = 1.5; ctx.setLineDash([]);
      ctx.beginPath();
      if (drawing.type === 'horizontal') {
        const yy = l.y(drawing.a.price); ctx.moveTo(l.left, yy); ctx.lineTo(l.w - l.right, yy); ctx.setLineDash([6, 4]);
      } else {
        ctx.moveTo(l.x(aIndex), l.y(drawing.a.price));
        ctx.lineTo(l.x(bIndex), l.y(drawing.b.price));
      }
      ctx.stroke(); ctx.setLineDash([]);
      for (const point of drawing.type === 'horizontal' ? [] : [drawing.a, drawing.b]) {
        ctx.fillStyle = this.palette.bg; ctx.beginPath(); ctx.arc(l.x(this.indexAtTime(point.time)), l.y(point.price), 3, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      }
    }
    const pendingIndex = this.pendingPoint ? this.indexAtTime(this.pendingPoint.time) : null;
    if (this.pendingPoint && pendingIndex !== null) {
      ctx.strokeStyle = '#61a2f5'; ctx.fillStyle = this.palette.bg; ctx.beginPath();
      ctx.arc(l.x(pendingIndex), l.y(this.pendingPoint.price), 3.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
  }

  drawMarkers(ctx, l) {
    const visible = this.markers.filter(marker => marker.price >= l.min && marker.price <= l.max)
      .map(marker => ({ ...marker, y: l.y(marker.price) })).filter(marker => marker.y >= l.top && marker.y <= l.priceBottom)
      .sort((a,b) => a.y-b.y);
    if (!visible.length) return;
    ctx.save();
    ctx.font = '500 9px "JetBrains Mono", Consolas, monospace';
    ctx.textBaseline = 'middle'; ctx.textAlign = 'left'; ctx.lineWidth = 1;
    const labelLimit = Math.max(0, Math.floor((l.priceBottom-l.top)/18));
    const labeled = visible.filter(marker=>marker.label).slice(0,labelLimit);
    for(let i=0;i<labeled.length;i++) labeled[i].labelY=Math.max(labeled[i].y-10,i?labeled[i-1].labelY+18:l.top+9);
    for(let i=labeled.length-1;i>=0;i--) labeled[i].labelY=Math.min(labeled[i].labelY,i<labeled.length-1?labeled[i+1].labelY-18:l.priceBottom-9);
    for (const marker of visible) {
      ctx.strokeStyle = '#95a7bc'; ctx.strokeStyle = marker.color;
      const color = ctx.strokeStyle;
      ctx.setLineDash([5, 5]);ctx.globalAlpha = .74;
      ctx.beginPath();ctx.moveTo(l.left, marker.y + .5);ctx.lineTo(l.w-l.right-1, marker.y + .5);ctx.stroke();
      ctx.setLineDash([]);ctx.globalAlpha = 1;
      if (!marker.label || !finite(marker.labelY)) continue;
      const maxWidth = Math.max(0, Math.min(190, l.plotW * .45));
      if (maxWidth < 35) continue;
      let text = marker.label;
      while (text.length > 1 && ctx.measureText(text).width > maxWidth-12) text = text.slice(0,-1);
      if (text !== marker.label) text = text.slice(0,-1) + '…';
      const labelY = marker.labelY;
      const labelW = Math.min(maxWidth, ctx.measureText(text).width+12), labelX = l.left+5;
      // Labels remain inside the plot, leaving the price axis and last-price
      // tag untouched. The enclosing price-plot clip also hides off-scale marks.
      ctx.fillStyle = this.palette.bg;ctx.globalAlpha = .93;ctx.fillRect(labelX,labelY-8,labelW,16);ctx.globalAlpha = 1;
      ctx.fillStyle = color;ctx.fillText(text,labelX+6,labelY);
    }
    ctx.restore();
  }

  renderOverlay() {
    const ctx = this.over;
    ctx.clearRect(0, 0, this.width, this.height);
    if (!this.hover || !this.layout || !this.data.length) return;
    const l = this.layout, p = this.palette;
    const { x: mx, y: my } = this.hover;
    if (mx < l.left || mx > l.w - l.right || my < l.top - 20 || my > l.bottom) return;
    const index = Math.floor(this.view.start + (mx - l.left) / l.stepX);
    const actualCandle = this.data[index];
    const executionHit = this.hitExecution(this.hover);
    const exact = executionHit?.events.length === 1 ? executionHit.events[0] : null;
    const continuous = l.detailed || this.view.span * this.executionInterval < 300;
    const crossY = exact ? executionHit.y : my;
    const xx = exact ? executionHit.x : continuous ? mx : actualCandle ? Math.round(l.x(index)) + .5 : mx;
    ctx.strokeStyle = p.cross; ctx.lineWidth = 1; ctx.setLineDash([3, 4]); ctx.globalAlpha = .8;
    ctx.beginPath(); ctx.moveTo(xx, l.top - 20); ctx.lineTo(xx, l.bottom); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(l.left, crossY + .5); ctx.lineTo(l.w - l.right, crossY + .5); ctx.stroke();
    ctx.globalAlpha = 1; ctx.setLineDash([]);
    ctx.font = '10px "JetBrains Mono", Consolas, monospace'; ctx.textBaseline = 'middle';
    const isPrice = my <= l.priceBottom;
    if (isPrice || l.showRsi && my > l.rsiTop + 25) {
      let label;
      if (isPrice) label = this.priceText(l.priceAtY(crossY));
      else label = clamp(100 - (my - l.rsiTop - 27) / Math.max(20, l.bottom - l.rsiTop - 32) * 100, 0, 100).toFixed(2);
      ctx.fillStyle = p.tag; ctx.fillRect(l.w - l.right + 1, crossY - 11, l.right - 1, 22);
      ctx.fillStyle = p.strong; ctx.fillText(label, l.w - l.right + 8, crossY);
    }
    const cursorTime = exact?.time ?? (continuous ? executionTimeAtIndex(this.data, this.view.start - .5 + (mx - l.left) / l.stepX, this.executionInterval) : actualCandle?.time * 1000);
    if (!finite(cursorTime)) return;
    const label = (this.exportTimeDisplay ?? displayTime).dateTime(cursorTime, { year: false, seconds: !!(exact || continuous), milliseconds: !!(exact || continuous) });
    const tagW = Math.min(l.plotW, Math.max(113, Math.ceil(ctx.measureText(label).width) + 20)), tagX = clamp(xx - tagW / 2, l.left, l.w - l.right - tagW);
    ctx.fillStyle = p.tag; ctx.fillRect(tagX, l.bottom + 1, tagW, 26);
    ctx.fillStyle = p.strong; ctx.textAlign = 'center'; ctx.fillText(label, tagX + tagW / 2, l.h - 12); ctx.textAlign = 'left';
  }

  localPoint(e) {
    const r = this.overlay.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  pointAt(point) {
    const l = this.layout;
    if (!l || !this.data.length) return null;
    const index = clamp(Math.floor(this.view.start + (point.x-l.left)/l.stepX),0,this.data.length-1);
    return { time: this.data[index].time, price: l.priceAtY(point.y) };
  }

  indexAtTime(time) {
    if (!finite(time) || !this.data.length || time < this.data[0].time || time > this.data.at(-1).time) return null;
    let lo=0,hi=this.data.length-1;
    while (lo < hi) {const mid=Math.floor((lo+hi)/2);if(this.data[mid].time < time)lo=mid+1;else hi=mid;}
    if (lo===0 || this.data[lo].time===time) return lo;
    return time-this.data[lo-1].time <= this.data[lo].time-time ? lo-1 : lo;
  }

  syncKeyboardHover() {
    const index = this.indexAtTime(this.keyboardTime);
    if (index === null || !this.layout) {
      this.keyboardMode=false;this.keyboardTime=null;this.hover=null;this.hoverIndex=null;
      this.callbacks.onCrosshair?.(null);return;
    }
    this.hoverIndex=index;
    this.hover={x:this.layout.x(index),y:this.layout.y(this.data[index].close)};
  }

  keyDown(e) {
    if (document.activeElement !== this.overlay || e.ctrlKey || e.metaKey || e.altKey || !this.data.length || !this.layout) return;
    if (['e', 'E', '[', ']', 'Enter'].includes(e.key)) {
      e.preventDefault(); e.stopPropagation();
      const eligible = this.executions.filter(event => this.executionIndex(event) !== null);
      if (!eligible.length) return;
      const selected = eligible.findIndex(event => event.id === this.selectedExecutionId);
      const next = e.key === '[' ? Math.max(0, (selected < 0 ? eligible.length : selected) - 1) : e.key === ']' ? Math.min(eligible.length - 1, selected + 1) : selected >= 0 && e.key === 'Enter' ? selected : eligible.length - 1;
      const event = eligible[next];
      this.focusExecution(event.id, { windowSeconds: Math.min(10, this.view.span * this.executionInterval) });
      const index = this.executionIndex(event);
      this.callbacks.onExecutionSelect?.({ events: [{ ...event }], x: this.layout.x(index), y: this.layout.y(event.price), grouped: false });
      return;
    }
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    e.preventDefault();e.stopPropagation();
    this.autoFit = false;
    this.executionFocus = false;
    if (this.view.span < 3) this.view.span = 20;
    const current = this.hoverIndex !== null ? this.hoverIndex : clamp(Math.floor(this.view.start+this.view.span)-1,0,this.data.length-1);
    const index = clamp(current+(e.key==='ArrowLeft'?-1:1),0,this.data.length-1);
    if (index < this.view.start+1) this.view.start=index-1;
    else if (index > this.view.start+this.view.span-2) this.view.start=index-this.view.span+2;
    this.constrainView();this.keyboardMode=true;this.keyboardTime=this.data[index].time;this.hoverIndex=index;
    this.callbacks.onCrosshair?.(this.data[index]);this.schedule();
  }

  contextMenu(e) {
    if (!this.layout || !this.data.length) return;
    const point=this.localPoint(e),l=this.layout;
    if (point.x<l.left || point.x>l.w-l.right || point.y<l.top || point.y>l.priceBottom) return;
    const at=this.pointAt(point);
    if (!at || !finite(at.price) || at.price<=0) return;
    e.preventDefault();
    this.callbacks.onContextMenu?.({price:at.price,time:at.time,clientX:e.clientX,clientY:e.clientY});
  }

  pointerDown(e) {
    if (!this.layout || !this.data.length || e.button !== 0) return;
    const point = this.localPoint(e), l = this.layout;
    if (point.x < l.left || point.x > l.w - l.right || point.y < l.top - 20 || point.y > l.bottom) return;
    this.overlay.focus({preventScroll:true});
    this.keyboardMode=false;this.keyboardTime=null;
    this.overlay.setPointerCapture?.(e.pointerId);
    if (this.tool !== 'crosshair' && point.y <= l.priceBottom) {
      const at = this.pointAt(point);
      if (!at || at.price <= 0) return;
      if (this.tool === 'horizontal') {
        this.drawings.push({ type: 'horizontal', a: at });
        this.drawings=this.drawings.slice(-100);
        this.callbacks.onDraw?.(this.getDrawings());
      } else if (!this.pendingPoint) this.pendingPoint = at;
      else {
        this.drawings.push({ type: 'trend', a: this.pendingPoint, b: at });
        this.drawings=this.drawings.slice(-100);
        this.pendingPoint = null;
        this.callbacks.onDraw?.(this.getDrawings());
      }
      this.schedule(); return;
    }
    this.drag = { x: point.x, y: point.y, start: this.view.start, active: false, pointer: e.pointerId, touch: e.pointerType === 'touch' };
  }

  pointerMove(e) {
    if (!this.layout || !this.data.length) return;
    this.keyboardMode=false;this.keyboardTime=null;
    const point = this.localPoint(e);
    this.hover = point;
    if (this.drag) {
      const dx = point.x - this.drag.x, dy = point.y - this.drag.y;
      if (Math.abs(dx) > 4 && (!this.drag.touch || Math.abs(dx) > Math.abs(dy))) this.drag.active = true;
      if (this.drag.active) {
        this.autoFit = false;
        this.overlay.style.cursor = 'grabbing';
        this.view.start = this.drag.start - dx / this.layout.stepX;
        this.constrainView(); this.schedule();
      }
    }
    const i = Math.floor(this.view.start + (point.x - this.layout.left) / this.layout.stepX);
    const valid = point.x >= this.layout.left && point.x <= this.layout.w - this.layout.right && point.y >= this.layout.top - 20 && point.y <= this.layout.bottom && i >= 0 && i < this.data.length;
    const selected = valid ? i : null;
    if (this.hoverIndex !== selected) { this.hoverIndex = selected; this.callbacks.onCrosshair?.(selected === null ? null : this.data[selected]); }
    const hit = this.drag?.active ? null : this.hitExecution(point);
    if (!this.drag?.active) this.overlay.style.cursor = hit ? 'pointer' : 'crosshair';
    this.notifyExecutionHover(hit);
    if (this.pendingPoint) this.schedule();
    else this.renderOverlay();
  }

  pointerUp(e) {
    if (this.drag && !this.drag.active && this.tool === 'crosshair') this.selectExecutionGlyph(this.hitExecution(this.localPoint(e)));
    this.drag = null;
    this.overlay.style.cursor = 'crosshair';
    if (this.overlay.hasPointerCapture?.(e.pointerId)) this.overlay.releasePointerCapture(e.pointerId);
  }

  pointerLeave() {
    if (this.drag?.active || this.keyboardMode && document.activeElement === this.overlay) return;
    this.hover = null; this.hoverIndex = null;
    this.notifyExecutionHover(null);
    this.callbacks.onCrosshair?.(null);
    this.renderOverlay();
  }

  exportImage({ subtitle = `${this.asset?.sourceLabel || 'Dados observados'} · QHOS` } = {}) {
    const previous = this.exportTimeDisplay;
    this.exportTimeDisplay = displayTime.forZone('UTC');
    try {
      this.render();
      const out = document.createElement('canvas');
      out.width = this.canvas.width; out.height = this.canvas.height;
      const c = out.getContext('2d');
      c.drawImage(this.canvas, 0, 0);
      c.scale(this.dpr, this.dpr);
      c.fillStyle = this.palette.strong;
      c.font = '600 13px Arial, sans-serif';
      c.fillText(`${this.asset?.displaySymbol || this.asset?.symbol || ''} · QHOS TERMINAL`, 16, 25);
      c.font = '10px Arial, sans-serif';
      c.fillStyle = this.palette.text;
      c.fillText(String(subtitle).slice(0, 120), 16, 44);
      return out.toDataURL('image/png');
    } finally {
      this.exportTimeDisplay = previous;
      this.render();
    }
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this.frame);
    this.resizeObserver.disconnect();
    this.overlay.removeEventListener('pointermove', this.handlers.move);
    this.overlay.removeEventListener('pointerdown', this.handlers.down);
    this.overlay.removeEventListener('pointerup', this.handlers.up);
    this.overlay.removeEventListener('pointercancel', this.handlers.cancel);
    this.overlay.removeEventListener('pointerleave', this.handlers.leave);
    this.overlay.removeEventListener('wheel', this.handlers.wheel);
    this.overlay.removeEventListener('dblclick', this.handlers.dblclick);
    this.overlay.removeEventListener('contextmenu', this.handlers.contextmenu);
    this.overlay.removeEventListener('keydown', this.handlers.keydown);
    this.overlay.removeEventListener('blur', this.handlers.blur);
    this.canvas.remove(); this.overlay.remove();
  }
}
