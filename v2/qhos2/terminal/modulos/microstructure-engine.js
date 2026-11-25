// Descriptive research on observed data. L2 changes are not individual orders.
const finite = value => typeof value === 'number' && Number.isFinite(value);
const positive = value => finite(value) && value > 0;
const clamp = (value, lo, hi) => Math.max(lo, Math.min(hi, value));
const fail = message => { throw new Error(message); };
const clean = (value, length = 100) => typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, length) : '';
const sum = values => values.reduce((total, value) => total + value, 0);
const mean = values => values.length ? sum(values) / values.length : null;
const midpoint = book => book ? (book.bids[0][0] + book.asks[0][0]) / 2 : null;
const MAX_BOOK_GAP = 2500;

function normalizedTrades(input = [], until = Infinity) {
  const seen = new Set();
  return (Array.isArray(input) ? input : []).filter(trade => {
    if (!trade || !positive(trade.time) || trade.time > until || !positive(trade.price) || trade.price > 1e12 || !positive(trade.size) || trade.size > 1e12 || !['buy', 'sell'].includes(trade.side)) return false;
    const key = trade.id == null ? null : `${trade.symbol || ''}:${trade.id}`;
    if (key && seen.has(key)) return false;
    if (key) seen.add(key);
    return true;
  }).map(trade => ({ ...trade })).sort((a, b) => a.time - b.time).slice(-20000);
}

function normalizedBooks(input = [], until = Infinity) {
  const out = [];
  for (const book of Array.isArray(input) ? input : []) {
    if (!book || book.ready !== true || !positive(book.time) || book.time > until) continue;
    const levels = side => {
      if (!Array.isArray(book[side]) || !book[side].length) return null;
      const ids = new Set(), rows = [];
      for (const row of book[side].slice(0, 40)) {
        if (!Array.isArray(row) || !positive(row[0]) || !positive(row[1]) || ids.has(row[0])) return null;
        ids.add(row[0]); rows.push([row[0], row[1]]);
      }
      return rows.sort((a, b) => side === 'bids' ? b[0] - a[0] : a[0] - b[0]);
    };
    const bids = levels('bids'), asks = levels('asks');
    if (!bids || !asks || bids[0][0] >= asks[0][0]) continue;
    out.push({ time: book.time, bids, asks });
  }
  return out.sort((a, b) => a.time - b.time).filter((book, index, all) => index === all.length - 1 || book.time !== all[index + 1].time).slice(-600);
}
function cutoff(until, trades, books) {
  if (finite(until)) return until;
  return Math.max(0, ...(Array.isArray(trades) ? trades : []).map(row => finite(row?.time) ? row.time : 0), ...(Array.isArray(books) ? books : []).map(row => finite(row?.time) ? row.time : 0));
}
function liquidityChange(previous, latest) {
  const empty = { available: false, reason: 'São necessários dois snapshots próximos.', elapsedMs: null, bidAdded: null, bidRemoved: null, askAdded: null, askRemoved: null, changedLevels: 0, comparedLevels: 0, coverage: 'common-prices-only' };
  if (!previous || !latest) return empty;
  const elapsedMs = latest.time - previous.time;
  if (elapsedMs <= 0 || elapsedMs > MAX_BOOK_GAP) return { ...empty, elapsedMs, reason: 'Lacuna entre snapshots; variação não calculada.' };
  const result = { ...empty, available: true, reason: '', elapsedMs, bidAdded: 0, bidRemoved: 0, askAdded: 0, askRemoved: 0 };
  for (const [side, prefix] of [['bids', 'bid'], ['asks', 'ask']]) {
    const before = new Map(previous[side]);
    for (const [price, size] of latest[side]) {
      if (!before.has(price)) continue;
      result.comparedLevels++;
      const difference = size - before.get(price);
      if (difference !== 0) result.changedLevels++;
      result[`${prefix}${difference >= 0 ? 'Added' : 'Removed'}`] += Math.abs(difference);
    }
  }
  if (!result.comparedLevels) return { ...empty, elapsedMs, reason: 'Os snapshots não compartilham níveis de preço.' };
  return result;
}

export function analyzeMicrostructure({ trades = [], books = [], until, windowMs = 60000 } = {}) {
  const to = cutoff(until, trades, books), window = positive(windowMs) ? clamp(windowMs, 100, 3600000) : 60000, from = to - window;
  const allTrades = normalizedTrades(trades, to), recent = allTrades.filter(trade => trade.time >= from);
  const snapshots = normalizedBooks(books, to), latest = snapshots.at(-1), previous = snapshots.at(-2);
  const buys = recent.filter(trade => trade.side === 'buy'), sells = recent.filter(trade => trade.side === 'sell');
  const buyVolume = sum(buys.map(trade => trade.size)), sellVolume = sum(sells.map(trade => trade.size)), volume = buyVolume + sellVolume;
  const bestBid = latest?.bids[0][0] ?? null, bestAsk = latest?.asks[0][0] ?? null, mid = midpoint(latest);
  const bidSize = latest ? sum(latest.bids.map(row => row[1])) : null, askSize = latest ? sum(latest.asks.map(row => row[1])) : null;
  const spread = latest ? bestAsk - bestBid : null, bookAgeMs = latest ? to - latest.time : null, bookFresh = bookAgeMs !== null && bookAgeMs <= MAX_BOOK_GAP;
  const dynamics = liquidityChange(previous, latest);
  const indicators = [];
  const shortTrades = recent.filter(trade => trade.time >= to - 1000);
  let sweep = null;
  for (const side of ['buy', 'sell']) {
    const rows = shortTrades.filter(trade => trade.side === side), prices = new Set(rows.map(trade => trade.price));
    const monotonic = rows.every((row, index) => !index || (row.price - rows[index - 1].price) * (side === 'buy' ? 1 : -1) >= 0);
    if (rows.length >= 3 && prices.size >= 3 && monotonic) sweep = { kind: 'sweep', status: 'candidate', side, count: rows.length, description: `${rows.length} negócios ${side === 'buy' ? 'compradores' : 'vendedores'} em ${prices.size} preços na direção da agressão, em até 1 s. Isso não identifica uma única ordem.` };
  }
  indicators.push(sweep ?? { kind: 'sweep', status: shortTrades.length < 3 ? 'insufficient' : 'not-detected', side: null, description: 'Procura três ou mais negócios em três preços progressivos no último segundo; não comprova uma ordem varrendo o livro.' });
  const flow2s = recent.filter(trade => trade.time >= to - 2000), buy2s = sum(flow2s.filter(row => row.side === 'buy').map(row => row.size)), sell2s = sum(flow2s.filter(row => row.side === 'sell').map(row => row.size));
  const absorptionReady = bookFresh && dynamics.available && flow2s.length >= 5 && previous.time >= to - 2500;
  const dominant = buy2s >= sell2s ? 'buy' : 'sell', share = Math.max(buy2s, sell2s) / Math.max(buy2s + sell2s, 1e-30);
  const stableMid = absorptionReady && Math.abs(mid - midpoint(previous)) <= Math.max(spread / 2, Number.EPSILON * mid);
  indicators.push({ kind: 'absorption', status: !absorptionReady ? 'insufficient' : share >= .75 && stableMid ? 'candidate' : 'not-detected', side: absorptionReady ? dominant : null, description: absorptionReady && share >= .75 && stableMid ? 'Agressão concentrada com pouca mudança do mid observado. Candidato descritivo; não confirma absorção ou identidade passiva.' : 'Requer cinco negócios recentes, agressão dominante e dois snapshots contínuos para comparar a estabilidade do mid.' });
  const beforeVisible = previous ? sum([...previous.bids, ...previous.asks].map(row => row[1])) : 0;
  const removed = dynamics.available ? dynamics.bidRemoved + dynamics.askRemoved : 0;
  const vacuum = bookFresh && dynamics.available && beforeVisible > 0 && removed / beforeVisible >= .3 && spread >= (previous.asks[0][0] - previous.bids[0][0]) * 1.5;
  indicators.push({ kind: 'vacuum', status: !bookFresh || !dynamics.available ? 'insufficient' : vacuum ? 'candidate' : 'not-detected', side: vacuum ? dynamics.bidRemoved > dynamics.askRemoved ? 'bid' : 'ask' : null, description: vacuum ? 'Redução relevante em preços comuns e spread mais largo. Pode refletir cancelamentos, execuções ou mudança da cobertura L2.' : 'Procura queda de pelo menos 30% da quantidade visível e aumento de spread entre snapshots próximos.' });
  return { available: recent.length > 0 || !!latest, from, to, windowMs: window, tradeCount: recent.length,
    buyVolume, sellVolume, delta: buyVolume - sellVolume, vwap: volume > 0 ? sum(recent.map(trade => trade.price * trade.size)) / volume : null,
    lastTradeTime: recent.at(-1)?.time ?? null, bestBid, bestAsk, mid, spread, spreadBps: mid ? spread / mid * 10000 : null,
    bidSize, askSize, imbalance: latest && bidSize + askSize > 0 ? (bidSize - askSize) / (bidSize + askSize) : null,
    bookTime: latest?.time ?? null, bookAgeMs, bookFresh, liquidityDynamics: dynamics, indicators,
    notes: ['Somente observações retidas até o cursor; janela parcial.', 'Variação de L2 compara preços comuns: não distingue inserção, cancelamento e execução de ordens individuais.', 'Candidatos são condições descritivas fixas, sem probabilidade preditiva ou validação estatística.'] };
}

export function runEventStudy({ trades = [], books = [], until, side = 'all', condition = side, minSize = 0, windowMs = 1000, maxEvents = 200 } = {}) {
  const to = cutoff(until, trades, books), window = positive(windowMs) ? clamp(windowMs, 100, 60000) : 1000;
  const floor = finite(minSize) && minSize > 0 ? minSize : 0, count = Math.floor(clamp(Number(maxEvents) || 200, 1, 200));
  const snapshots = normalizedBooks(books, to), observations = normalizedTrades(trades, to).filter(trade => (condition === 'all' || trade.side === condition) && trade.size >= floor).slice(-count);
  const rows = observations.map(trade => {
    const preTarget = trade.time - window, postTarget = trade.time + window;
    let preIndex = -1, postIndex = -1;
    for (let i = 0; i < snapshots.length; i++) { if (snapshots[i].time <= preTarget) preIndex = i; if (postIndex < 0 && snapshots[i].time >= postTarget) postIndex = i; }
    const pre = snapshots[preIndex], post = snapshots[postIndex];
    const preValid = !!pre && preTarget - pre.time <= 1500, postValid = !!post && post.time - postTarget <= 1500;
    let reason = !preValid ? 'Sem snapshot próximo anterior à janela.' : !postValid ? 'Janela posterior ainda não observada.' : '';
    if (!reason) for (let i = preIndex + 1; i <= postIndex; i++) if (snapshots[i].time - snapshots[i - 1].time > MAX_BOOK_GAP) { reason = 'Lacuna de snapshots atravessa a janela.'; break; }
    const preMid = preValid ? midpoint(pre) : null, postMid = postValid ? midpoint(post) : null;
    return { id: String(trade.id ?? `${trade.time}:${trade.price}`), tradeId: trade.id == null ? null : String(trade.id), symbol: trade.symbol ?? null, time: trade.time, side: trade.side, size: trade.size, qty: trade.size, price: trade.price,
      preMid, postMid, preTime: preValid ? pre.time : null, postTime: postValid ? post.time : null,
      returnBps: reason ? null : (postMid / preMid - 1) * 10000, status: reason ? 'missing' : 'complete', reason };
  });
  const complete = rows.filter(row => row.status === 'complete'), values = complete.map(row => row.returnBps).sort((a, b) => a - b);
  const medianReturnBps = values.length ? values.length % 2 ? values[(values.length - 1) / 2] : (values[values.length / 2 - 1] + values[values.length / 2]) / 2 : null;
  return { rows, windowMs: window, summary: { events: rows.length, complete: complete.length, missing: rows.length - complete.length, meanReturnBps: mean(values), medianReturnBps, positiveShare: values.length ? values.filter(value => value > 0).length / values.length : null },
    assumptions: { condition, minSize: floor, windowMs: window, maxEvents: count, until: to, toleranceMs: 1500, maxBookGapMs: MAX_BOOK_GAP },
    notes: ['Retorno do mid entre snapshots próximos de t−janela e t+janela; não representa lucro executável.', 'Sem interpolação ou uso de snapshots posteriores ao cursor. Eventos sobrepostos não são observações independentes.', 'Amostra descritiva, sem teste de significância ou inferência causal.'] };
}

export function runExperiments({ candles = [], until, intervalSeconds, capital = 100000, initialEquity = capital, quantity = 1, feeBps = 10, slippageTicks = 1, tickSize = .01 } = {}) {
  if (!positive(initialEquity) || initialEquity > 1e15 || !positive(quantity) || quantity > 1e12 || !finite(feeBps) || feeBps < 0 || feeBps > 1000 || !finite(slippageTicks) || slippageTicks < 0 || slippageTicks > 100000 || !positive(tickSize)) fail('Capital, quantidade e custos do experimento são inválidos.');
  const all = (Array.isArray(candles) ? candles : []).filter(row => row && positive(row.time) && (!finite(until) || row.time * 1000 < until) && ['open', 'high', 'low', 'close'].every(key => positive(row[key])) && row.low <= Math.min(row.open, row.close) && row.high >= Math.max(row.open, row.close) && row.low <= row.high).map(row => ({ ...row })).sort((a, b) => a.time - b.time).filter((row, i, rows) => !i || row.time !== rows[i - 1].time);
  const differences = all.slice(1).map((row, i) => row.time - all[i].time).filter(positive);
  const interval = positive(intervalSeconds) ? intervalSeconds : differences.length ? Math.min(...differences) : null;
  const to = finite(until) ? until : all.length && interval ? (all.at(-1).time + interval) * 1000 : 0;
  const data = all.filter(row => row.partial !== true && interval && (row.time + interval) * 1000 <= to).slice(-500);
  const assumptions = { initialEquity, capital: initialEquity, quantity, feeBps, slippageTicks, tickSize, until: to, intervalSeconds: interval, warmupBars: 10, execution: 'next-bar-open', finalExit: 'last-completed-close', capitalModel: 'notional-1x', grossIncludesSlippage: true };
  const notes = ['Hipóteses históricas simples e determinísticas; não são estratégias validadas.', 'Sinais calculados em candles fechados; execução apenas na abertura seguinte. Posição restante é encerrada no último fechamento disponível.', 'Custos por execução e slippage fixo; sem fila, latência, impacto, financiamento ou custos de aluguel.', 'Candles ausentes interrompem o aquecimento de sinais; não são preenchidos artificialmente.'];
  if (!interval || data.length < 12) return { available: false, results: [], assumptions, notes: ['São necessários pelo menos 12 candles fechados válidos.', ...notes] };
  if (!finite(slippageTicks * tickSize) || slippageTicks * tickSize >= Math.min(...data.map(row => Math.min(row.open, row.close)))) fail('Slippage precisa ser menor que os preços observados.');
  const strategies = [{ id: 'trend', name: 'Tendência MA5 / MA10' }, { id: 'reversion', name: 'Reversão à MA10' }, { id: 'benchmark', name: 'Compra e manutenção' }];
  const results = strategies.map(strategy => {
    let balance = initialEquity, position = null, grossPnl = 0, fees = 0, slippageCost = 0, entries = 0, exits = 0, rejected = 0, contiguous = 1, benchmarkEntered = false;
    const executions = [], curve = [{ time: data[0].time * 1000, equity: initialEquity }];
    const execute = (side, rawPrice, time, reason, closing = false) => {
      const slip = slippageTicks * tickSize, price = rawPrice + (side === 'buy' ? slip : -slip), fee = price * quantity * feeBps / 10000;
      if (!positive(price) || !finite(fee)) return false;
      if (!closing && price * quantity + fee > balance + 1e-8) { rejected++; return false; }
      if (closing) { const pnl = (price - position.price) * quantity * position.direction; balance += pnl - fee; grossPnl += pnl; exits++; position = null; }
      else { balance -= fee; entries++; position = { direction: side === 'buy' ? 1 : -1, price, qty: quantity, time }; }
      fees += fee; slippageCost += slip * quantity;
      executions.push({ time, side, price, rawPrice, qty: quantity, fee, reason });
      return true;
    };
    for (let i = 1; i < data.length; i++) {
      const bar = data[i], contiguousStep = Math.abs(bar.time - data[i - 1].time - interval) < 1e-7;
      contiguous = contiguousStep ? contiguous + 1 : 1;
      if (contiguous >= 11 && i >= 10) {
        const previous = data.slice(i - 10, i), ma10 = mean(previous.map(row => row.close)), ma5 = mean(previous.slice(-5).map(row => row.close));
        let target = strategy.id === 'trend' ? Math.sign(ma5 - ma10) : strategy.id === 'reversion' ? Math.sign(ma10 - previous.at(-1).close) : benchmarkEntered ? position?.direction ?? 0 : 1;
        if (strategy.id === 'benchmark' && benchmarkEntered && !position) target = 0;
        if (position && position.direction !== target) execute(position.direction === 1 ? 'sell' : 'buy', bar.open, bar.time * 1000, 'Mudança do sinal', true);
        if (target && !position) { const entered = execute(target === 1 ? 'buy' : 'sell', bar.open, bar.time * 1000, strategy.id === 'benchmark' ? 'Referência após aquecimento' : 'Sinal do candle anterior'); if (strategy.id === 'benchmark' && entered) benchmarkEntered = true; }
      }
      const nav = balance + (position ? (bar.close - position.price) * position.qty * position.direction : 0);
      curve.push({ time: (bar.time + interval) * 1000, equity: nav });
    }
    const last = data.at(-1);
    if (position) execute(position.direction === 1 ? 'sell' : 'buy', last.close, (last.time + interval) * 1000, 'Encerramento da amostra', true);
    curve[curve.length - 1] = { time: (last.time + interval) * 1000, equity: balance };
    return { ...strategy, netNAV: balance, grossPnl, netPnl: balance - initialEquity, fees, slippageCost, trades: exits, entries, exits, position: 0, rejected, curve, executions };
  });
  return { available: true, results, assumptions, notes };
}

const MBO_FORMAT = 'qhos-mbo-research-v1';
const MBO_LIMITS = Object.freeze({ bytes: 10 * 1024 * 1024, orders: 20000, events: 50000 });
const unsigned = (value, label, allowZero = true) => {
  if (typeof value !== 'string' || !/^(?:0|[1-9]\d{0,39})$/.test(value)) fail(`${label}: use uma string inteira decimal de até 40 dígitos.`);
  const number = BigInt(value);
  if (!allowZero && number === 0n) fail(`${label}: precisa ser positivo.`);
  return number;
};
const safeSize = (value, label, allowZero = false) => { if (!Number.isSafeInteger(value) || value < (allowZero ? 0 : 1) || value > 1000000000000) fail(`${label}: quantidade inteira inválida.`); return value; };
const orderId = value => { if (typeof value !== 'string' || !/^[A-Za-z0-9._:-]{1,100}$/.test(value)) fail('Identificador de ordem inválido.'); return value; };
const sideValue = value => { if (value !== 'buy' && value !== 'sell') fail('Lado de ordem deve ser buy ou sell.'); return value; };
function decimalPrice(ticks, tickSize) {
  const [whole, fraction = ''] = tickSize.split('.'), scale = fraction.length;
  const units = BigInt(whole + fraction), total = BigInt(ticks) * units, digits = total.toString().padStart(scale + 1, '0');
  return scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits;
}
function freezeDeep(value) { if (value && typeof value === 'object' && !Object.isFrozen(value)) { for (const item of Object.values(value)) freezeDeep(item); Object.freeze(value); } return value; }

// Lazy invalidation keeps FIFO checks bounded even for large modify/cancel archives.
function fifoHeap(side, state) {
  const heap = [];
  const before = (a, b) => a.price === b.price ? a.priority < b.priority : side === 'buy' ? a.price > b.price : a.price < b.price;
  const removeFirst = () => {
    const last = heap.pop();
    if (!heap.length) return;
    heap[0] = last;
    let index = 0;
    while (index * 2 + 1 < heap.length) {
      let child = index * 2 + 1;
      if (child + 1 < heap.length && before(heap[child + 1], heap[child])) child++;
      if (!before(heap[child], heap[index])) break;
      [heap[index], heap[child]] = [heap[child], heap[index]]; index = child;
    }
  };
  return {
    add(order) {
      heap.push({ order, price: BigInt(order.priceTicks), priority: BigInt(order.priority) });
      let index = heap.length - 1;
      while (index > 0) {
        const parent = Math.floor((index - 1) / 2);
        if (!before(heap[index], heap[parent])) break;
        [heap[parent], heap[index]] = [heap[index], heap[parent]]; index = parent;
      }
    },
    first() {
      while (heap.length && state.get(heap[0].order.id) !== heap[0].order) removeFirst();
      return heap[0]?.order ?? null;
    }
  };
}
export const MBO_ARCHIVE_TEMPLATE = freezeDeep({ format: MBO_FORMAT, symbol: 'BTCUSD', venue: 'IMPORTADO', priority: 'fifo', tickSize: '0.01', snapshot: { sequence: '0', timeNs: '0', orders: [] }, events: [] });

export function parseMBOArchive(input) {
  let raw, text;
  try { text = typeof input === 'string' ? input : JSON.stringify(input); } catch { fail('O arquivo MBO precisa ser JSON serializável.'); }
  if (typeof text !== 'string' || new TextEncoder().encode(text).length > MBO_LIMITS.bytes) fail('O arquivo MBO excede 10 MB ou está vazio.');
  try { raw = JSON.parse(text); } catch { fail('JSON MBO inválido.'); }
  if (!raw || raw.format !== MBO_FORMAT || raw.priority !== 'fifo') fail('Importe um arquivo qhos-mbo-research-v1 com priority:"fifo" explícito. L2 não contém fila individual.');
  const symbol = clean(raw.symbol, 40), venue = clean(raw.venue, 80);
  if (!symbol || !venue || symbol !== raw.symbol || venue !== raw.venue) fail('Ativo e venue precisam ser textos válidos.');
  const tickSize = raw.tickSize;
  if (typeof tickSize !== 'string' || !/^(?:0|[1-9]\d{0,8})(?:\.\d{1,12})?$/.test(tickSize) || BigInt(tickSize.replace('.', '')) === 0n) fail('tickSize deve ser decimal positivo em string, sem notação exponencial.');
  const snapshot = raw.snapshot;
  if (!snapshot || !Array.isArray(snapshot.orders) || snapshot.orders.length > MBO_LIMITS.orders || !Array.isArray(raw.events) || raw.events.length > MBO_LIMITS.events) fail('Snapshot ou eventos MBO inválidos: limite de 20.000 ordens e 50.000 eventos.');
  let sequence = unsigned(snapshot.sequence, 'Sequência inicial'), timeNs = unsigned(snapshot.timeNs, 'Horário inicial');
  const state = new Map(), initial = [], priorities = new Set();
  const queues = { buy: fifoHeap('buy', state), sell: fifoHeap('sell', state) };
  const putOrder = order => { state.set(order.id, order); queues[order.side].add(order); };
  let totalSize = 0n;
  const assertTotal = () => { if (totalSize > BigInt(Number.MAX_SAFE_INTEGER)) fail('Quantidade total MBO excede o intervalo inteiro exato.'); };
  for (const row of snapshot.orders) {
    if (!row || typeof row !== 'object') fail('Ordem inicial inválida.');
    const id = orderId(row.id), side = sideValue(row.side), priceTicks = unsigned(row.priceTicks, 'Preço em ticks', false), priority = unsigned(row.priority, 'Prioridade');
    const size = safeSize(row.size, 'Ordem inicial');
    if (priority > sequence) fail('Prioridade inicial precisa ser um ordinal menor ou igual à sequência do snapshot.');
    const priorityKey = `${side}:${priceTicks}:${priority}`;
    if (state.has(id) || priorities.has(priorityKey)) fail('ID repetido ou prioridade FIFO ambígua no mesmo preço.');
    const order = { id, side, priceTicks: priceTicks.toString(), size, priority: priority.toString() };
    totalSize += BigInt(size); assertTotal();
    priorities.add(priorityKey); putOrder({ ...order }); initial.push(order);
  }
  const events = [];
  for (const row of raw.events) {
    if (!row || typeof row !== 'object' || !['add', 'modify', 'cancel', 'execute'].includes(row.action)) fail('Ação MBO inválida.');
    const nextSequence = unsigned(row.sequence, 'Sequência de evento'), nextTime = unsigned(row.timeNs, 'Horário de evento');
    if (nextSequence !== sequence + 1n) fail('Lacuna ou duplicidade na sequência MBO. O arquivo não foi aplicado.');
    if (nextTime < timeNs) fail('Horários MBO precisam ser monotônicos.');
    const id = orderId(row.orderId), existing = state.get(id);
    const event = { sequence: nextSequence.toString(), timeNs: nextTime.toString(), action: row.action, orderId: id };
    if (row.action === 'add') {
      if (existing) fail('Add referencia uma ordem já ativa.');
      if (state.size >= MBO_LIMITS.orders) fail('O livro excede 20.000 ordens ativas.');
      const side = sideValue(row.side), priceTicks = unsigned(row.priceTicks, 'Preço em ticks', false).toString(), size = safeSize(row.size, 'Add');
      totalSize += BigInt(size); assertTotal();
      putOrder({ id, side, priceTicks, size, priority: nextSequence.toString() }); Object.assign(event, { side, priceTicks, size });
    } else {
      if (!existing) fail('Evento referencia ordem desconhecida ou já encerrada.');
      if (row.side != null && row.side !== existing.side) fail('O lado de uma ordem existente não pode ser alterado.');
      if (row.action === 'modify') {
        const priceTicks = row.priceTicks == null ? existing.priceTicks : unsigned(row.priceTicks, 'Preço em ticks', false).toString(), size = safeSize(row.size, 'Modify');
        const losesPriority = priceTicks !== existing.priceTicks || size > existing.size;
        totalSize += BigInt(size) - BigInt(existing.size); assertTotal();
        putOrder({ ...existing, priceTicks, size, priority: losesPriority ? nextSequence.toString() : existing.priority });
        Object.assign(event, { side: existing.side, priceTicks, size, losesPriority });
      } else if (row.action === 'cancel') {
        if (row.size != null && row.size !== existing.size) fail('Cancel remove a ordem inteira; use modify para redução parcial.');
        if (row.priceTicks != null && unsigned(row.priceTicks, 'Preço em ticks', false).toString() !== existing.priceTicks) fail('Preço de cancelamento não corresponde à ordem.');
        totalSize -= BigInt(existing.size);
        state.delete(id); Object.assign(event, { side: existing.side, priceTicks: existing.priceTicks, size: existing.size });
      } else {
        const size = safeSize(row.size, 'Execute');
        if (size > existing.size) fail('Execução excede a quantidade restante da ordem.');
        if (row.priceTicks != null && unsigned(row.priceTicks, 'Preço em ticks', false).toString() !== existing.priceTicks) fail('Preço de execução não corresponde à ordem.');
        if (queues[existing.side].first()?.id !== id) fail('Execução contradiz FIFO por preço e tempo: existe uma ordem prioritária no mesmo lado.');
        totalSize -= BigInt(size);
        if (size === existing.size) state.delete(id); else putOrder({ ...existing, size: existing.size - size });
        Object.assign(event, { side: existing.side, priceTicks: existing.priceTicks, size });
      }
    }
    events.push(event); sequence = nextSequence; timeNs = nextTime;
  }
  const orders = [...state.values()].map(row => ({ ...row, price: decimalPrice(row.priceTicks, tickSize) }));
  const bids = orders.filter(row => row.side === 'buy').sort((a, b) => BigInt(a.priceTicks) > BigInt(b.priceTicks) ? -1 : BigInt(a.priceTicks) < BigInt(b.priceTicks) ? 1 : 0);
  const asks = orders.filter(row => row.side === 'sell').sort((a, b) => BigInt(a.priceTicks) < BigInt(b.priceTicks) ? -1 : BigInt(a.priceTicks) > BigInt(b.priceTicks) ? 1 : 0);
  const summary = { sequence: sequence.toString(), timeNs: timeNs.toString(), orderCount: orders.length, eventCount: events.length,
    bidSize: sum(bids.map(row => row.size)), askSize: sum(asks.map(row => row.size)), bestBidTicks: bids[0]?.priceTicks ?? null, bestAskTicks: asks[0]?.priceTicks ?? null, bestBid: bids[0]?.price ?? null, bestAsk: asks[0]?.price ?? null,
    crossed: !!(bids.length && asks.length && BigInt(bids[0].priceTicks) >= BigInt(asks[0].priceTicks)) };
  if (summary.crossed) fail('O estado final do livro está travado ou cruzado. Este formato FIFO não modela leilões ou exceções de matching.');
  return freezeDeep({ format: MBO_FORMAT, symbol, venue, priority: 'fifo', tickSize, snapshot: { sequence: snapshot.sequence, timeNs: snapshot.timeNs, orders: initial }, events, orders, summary, sourceVerified: false,
    assumptions: ['Arquivo importado; origem não autenticada.', 'FIFO por preço e ordinal de prioridade: Execute exige a primeira ordem do melhor nível do lado. Não comprova o matching de uma bolsa.', 'Mudança de preço ou aumento de tamanho recebe a sequência do evento como nova prioridade; redução conserva prioridade.', 'Cancel remove a ordem inteira. Execute reduz a quantidade; não reconstrói liquidez oculta.', 'Estado final travado ou cruzado é rejeitado; o formato não representa leilões nem exceções de matching.'] });
}

export function queryQueue(archive, id) {
  if (!archive || archive.format !== MBO_FORMAT || archive.priority !== 'fifo' || !Array.isArray(archive.orders) || !archive.summary || archive.sourceVerified !== false) fail('A fila exige um arquivo MBO validado. Livro L2 agregado não contém ordens individuais.');
  if (archive.summary.crossed) fail('A fila exige um estado final de livro não cruzado.');
  const target = archive.orders.find(row => row.id === id);
  if (!target) return { available: false, reason: 'A ordem não está ativa no estado final do arquivo.', orderId: id, sourceVerified: false };
  const level = archive.orders.filter(row => row.side === target.side && row.priceTicks === target.priceTicks)
    .map(row => ({ ...row })).sort((a, b) => BigInt(a.priority) < BigInt(b.priority) ? -1 : BigInt(a.priority) > BigInt(b.priority) ? 1 : a.id.localeCompare(b.id));
  const index = level.findIndex(row => row.id === id);
  return freezeDeep({ available: true, orderId: id, side: target.side, priceTicks: target.priceTicks, price: target.price, size: target.size,
    position: index + 1, ordersAhead: index, quantityAhead: sum(level.slice(0, index).map(row => row.size)), levelOrders: level.length, levelQuantity: sum(level.map(row => row.size)), orders: level,
    asOfSequence: archive.summary.sequence, asOfTimeNs: archive.summary.timeNs, sourceVerified: false });
}
