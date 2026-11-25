/* Pure accounting and local bracket rules for QHOS paper trading.
 * The configurable 10 bps default is a simulation assumption, not a statement
 * about Coinbase's actual fees. This module never sends or modifies orders.
 */
export const DEFAULT_FEE_BPS = 10;

function numeric(value, label) {
  if (typeof value !== 'number' && typeof value !== 'string') throw new TypeError(`${label} deve ser um número.`);
  if (typeof value === 'string' && !value.trim()) throw new TypeError(`${label} deve ser um número.`);
  const result = Number(value);
  if (!Number.isFinite(result)) throw new RangeError(`${label} deve ser finito.`);
  return result;
}

function nonnegative(value, label) {
  const result = numeric(value, label);
  if (result < 0) throw new RangeError(`${label} não pode ser negativo.`);
  return result;
}

function positive(value, label) {
  const result = numeric(value, label);
  if (result <= 0) throw new RangeError(`${label} deve ser maior que zero.`);
  return result;
}

function finiteResult(value, label) {
  if (!Number.isFinite(value)) throw new RangeError(`${label} excede o limite numérico da simulação.`);
  return value;
}

function requireSide(side) {
  if (side !== 'buy' && side !== 'sell') throw new TypeError('O lado deve ser buy ou sell.');
  return side;
}

function optionalPrice(value, label) {
  return value == null || (typeof value === 'string' && !value.trim()) ? null : positive(value, label);
}

export function validateFeeBps(value = DEFAULT_FEE_BPS) {
  const rate = nonnegative(value, 'Taxa em pontos-base');
  if (rate > 10000) throw new RangeError('A taxa da simulação deve estar entre 0 e 10.000 pontos-base.');
  return rate;
}

export function feeFor(notional, feeBps = DEFAULT_FEE_BPS) {
  const amount = nonnegative(notional, 'Valor financeiro');
  return finiteResult(amount * (validateFeeBps(feeBps) / 10000), 'Taxa');
}

export function validateBrackets({ side, entryPrice, stopPrice, targetPrice } = {}) {
  let stop = null, target = null;
  try {
    requireSide(side);
    const entry = positive(entryPrice, 'Preço de referência da entrada');
    stop = optionalPrice(stopPrice, 'Preço de stop');
    target = optionalPrice(targetPrice, 'Preço de alvo');
    if (side === 'buy') {
      if (stop !== null && stop >= entry) throw new RangeError('Na compra, o stop deve ficar abaixo do preço de entrada.');
      if (target !== null && target <= entry) throw new RangeError('Na compra, o alvo deve ficar acima do preço de entrada.');
    } else {
      if (stop !== null && stop <= entry) throw new RangeError('Na venda, o stop deve ficar acima do preço de entrada.');
      if (target !== null && target >= entry) throw new RangeError('Na venda, o alvo deve ficar abaixo do preço de entrada.');
    }
    return { valid: true, error: null, stopPrice: stop, targetPrice: target };
  } catch (error) {
    return { valid: false, error: error.message, stopPrice: stop, targetPrice: target };
  }
}

function bestPrice(levels, side) {
  if (!Array.isArray(levels)) return null;
  let best = null;
  for (const level of levels) {
    if (!Array.isArray(level)) continue;
    const price = Number(level[0]), size = Number(level[1]);
    if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(size) || size <= 0) continue;
    if (best === null || (side === 'bid' ? price > best : price < best)) best = price;
  }
  return best;
}

/** Returns an instruction only. The caller must persist stopActive=true when a
 * stop first triggers, even when visible liquidity fills zero units. A latched
 * stop keeps market priority over the target until its remaining size is zero.
 * Freshness, symbol selection, replay, and connection health are caller gates.
 */
export function getExitSignal(position, book) {
  if (!position || !['buy', 'sell'].includes(position.side) || !Number.isFinite(position.qty) || position.qty <= 0) return null;
  if (position.symbol && book?.symbol && position.symbol !== book.symbol) return null;
  const side = position.side === 'buy' ? 'sell' : 'buy';
  if (position.stopActive === true) return { reason: 'stop', side };
  if (book?.ready !== true) return null;
  const bid = bestPrice(book.bids, 'bid'), ask = bestPrice(book.asks, 'ask');
  if (bid === null || ask === null || bid >= ask) return null;
  const quote = position.side === 'buy' ? bid : ask;
  const stop = typeof position.stopPrice === 'number' && Number.isFinite(position.stopPrice) && position.stopPrice > 0 ? position.stopPrice : null;
  const target = typeof position.targetPrice === 'number' && Number.isFinite(position.targetPrice) && position.targetPrice > 0 ? position.targetPrice : null;
  if (stop !== null && (position.side === 'buy' ? quote <= stop : quote >= stop)) return { reason: 'stop', side };
  if (target !== null && (position.side === 'buy' ? quote >= target : quote <= target)) return { reason: 'target', side, limitPrice: target };
  return null;
}

function fillAmounts(fill) {
  if (!fill || typeof fill !== 'object') throw new TypeError('Execução inválida.');
  const qty = nonnegative(fill.filled ?? 0, 'Quantidade executada');
  const notional = nonnegative(fill.notional ?? 0, 'Valor financeiro executado');
  if ((qty === 0) !== (notional === 0)) throw new RangeError('Quantidade e valor financeiro da execução são inconsistentes.');
  const averagePrice = qty > 0 ? finiteResult(notional / qty, 'Preço médio') : null;
  if (qty > 0 && averagePrice <= 0) throw new RangeError('Preço médio inválido.');
  return { qty, notional, averagePrice };
}

/** Capital check before a new entry. For IOC/market calls omit limitPrice and
 * remaining, or explicitly pass remaining:0. A pending limit reserves both
 * remaining notional and its assumed future execution fee; no fee is charged
 * until a fill actually happens.
 */
export function entryCapital(fill, { remaining, limitPrice, feeBps = DEFAULT_FEE_BPS } = {}) {
  const execution = fillAmounts(fill), rate = validateFeeBps(feeBps);
  const limit = optionalPrice(limitPrice, 'Preço limite');
  const pendingQty = nonnegative(remaining ?? (limit !== null ? fill.remaining ?? 0 : 0), 'Quantidade pendente');
  if (pendingQty > 0 && limit === null) throw new RangeError('Informe um preço limite para reservar a quantidade pendente.');
  const pendingNotional = finiteResult(pendingQty * (limit ?? 0), 'Reserva de capital');
  const entryFee = feeFor(execution.notional, rate), pendingFee = feeFor(pendingNotional, rate);
  const reserve = finiteResult(pendingNotional + pendingFee, 'Reserva com taxa');
  const total = finiteResult(execution.notional + entryFee + reserve, 'Capital necessário');
  return { total, notional: execution.notional, entryFee, pendingNotional, pendingFee, reserve, remaining: pendingQty, feeBps: rate };
}

/** Accounts for one full or partial exit without mutating either input.
 * Entry fees were already deducted from balance and realized at entry.
 * Apply only balanceDelta=grossPnl-exitFee at exit; netPnl also allocates the
 * already-paid entry fee for the trade report, never for a second deduction.
 * Missing entryFeeRemaining means a legacy position, with zero retroactive fee.
 * A position's snapshotted feeBps always overrides the current global setting.
 */
export function computeExit(position, fill, { feeBps = DEFAULT_FEE_BPS } = {}) {
  if (!position || typeof position !== 'object') throw new TypeError('Posição inválida.');
  const side = requireSide(position.side), positionQty = positive(position.qty, 'Quantidade da posição');
  const entryPrice = positive(position.price ?? position.entryPrice, 'Preço de entrada');
  const feeRate = validateFeeBps(position.feeBps ?? feeBps);
  const entryFeeRemaining = nonnegative(position.entryFeeRemaining ?? 0, 'Taxa de entrada ainda não alocada');
  const execution = fillAmounts(fill);
  const epsilon = Math.min(positionQty * 1e-12, 1e-10);
  if (execution.qty > positionQty + epsilon) throw new RangeError('A saída não pode exceder a quantidade aberta da posição.');
  let qty = Math.min(execution.qty, positionQty);
  let remaining = positionQty - qty;
  if (remaining <= epsilon) { qty = positionQty; remaining = 0; }
  if (qty === 0) return {
    qty:0, remaining:positionQty, allocatedEntryFee:0, remainingEntryFee:entryFeeRemaining,
    exitFee:0, grossPnl:0, netPnl:0, balanceDelta:0, feeBps:feeRate, notional:0, averagePrice:null, complete:false,
  };
  const notional = finiteResult(execution.averagePrice * qty, 'Valor financeiro da saída');
  const allocatedEntryFee = remaining === 0 ? entryFeeRemaining : entryFeeRemaining * (qty / positionQty);
  const remainingEntryFee = Math.max(0, entryFeeRemaining - allocatedEntryFee);
  const exitFee = feeFor(notional, feeRate);
  const grossPnl = finiteResult((notional - entryPrice * qty) * (side === 'buy' ? 1 : -1), 'Resultado bruto');
  const balanceDelta = finiteResult(grossPnl - exitFee, 'Variação do saldo');
  const netPnl = finiteResult(balanceDelta - allocatedEntryFee, 'Resultado líquido');
  return { qty, remaining, allocatedEntryFee, remainingEntryFee, exitFee, grossPnl, netPnl, balanceDelta,
    feeBps:feeRate, notional, averagePrice:execution.averagePrice, complete:remaining===0 };
}
