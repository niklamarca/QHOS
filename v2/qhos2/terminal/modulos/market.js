// All prices, market data, candles, volumes, and order books in this module are simulated.
export const ASSETS = [
  { symbol: 'BTCUSD', name: 'Bitcoin', market: 'Cripto', exchange: 'CRYPTO', currency: 'USD', price: 67432.80, change: 2.34, color: '#f7931a', icon: '₿' },
  { symbol: 'ETHUSD', name: 'Ethereum', market: 'Cripto', exchange: 'CRYPTO', currency: 'USD', price: 3521.45, change: 1.82, color: '#8b9ef5', icon: 'Ξ' },
  { symbol: 'SOLUSD', name: 'Solana', market: 'Cripto', exchange: 'CRYPTO', currency: 'USD', price: 173.64, change: -0.76, color: '#a391ff', icon: 'S' },
  { symbol: 'BNBUSD', name: 'BNB', market: 'Cripto', exchange: 'CRYPTO', currency: 'USD', price: 598.21, change: 1.13, color: '#f3ba2f', icon: 'B' },
  { symbol: 'SPX', name: 'S&P 500', market: 'Índices', exchange: 'INDEX', currency: 'USD', price: 5464.62, change: 0.77, color: '#5d8ffc', icon: 'S' },
  { symbol: 'NDX', name: 'Nasdaq 100', market: 'Índices', exchange: 'INDEX', currency: 'USD', price: 19908.86, change: 1.04, color: '#6bbbe8', icon: 'N' },
  { symbol: 'AAPL', name: 'Apple Inc.', market: 'Ações', exchange: 'NASDAQ', currency: 'USD', price: 214.29, change: -0.41, color: '#b4becd', icon: 'A' },
  { symbol: 'NVDA', name: 'NVIDIA', market: 'Ações', exchange: 'NASDAQ', currency: 'USD', price: 126.57, change: 2.84, color: '#86ca4c', icon: 'N' },
  { symbol: 'PETR4', name: 'Petrobras PN', market: 'Ações', exchange: 'B3', currency: 'BRL', price: 38.72, change: 1.16, color: '#48b98a', icon: 'P' },
  { symbol: 'VALE3', name: 'Vale ON', market: 'Ações', exchange: 'B3', currency: 'BRL', price: 62.18, change: -0.63, color: '#d9bb54', icon: 'V' },
  { symbol: 'EURUSD', name: 'Euro / Dólar', market: 'Forex', exchange: 'FOREX', currency: 'USD', price: 1.08764, change: -0.12, color: '#658dea', icon: '€' },
  { symbol: 'USDJPY', name: 'Dólar / Iene', market: 'Forex', exchange: 'FOREX', currency: 'JPY', price: 157.82, change: 0.24, color: '#df8290', icon: '¥' },
];

const ASSET_MAP = new Map(ASSETS.map(asset => [asset.symbol, asset]));
const INTERVALS = { '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1D': 86400, '1d': 86400 };
const END_TIME = Date.parse('2026-10-23T16:00:00Z') / 1000;

function seedFor(value) {
  let seed = 2166136261;
  for (const character of String(value)) {
    seed ^= character.charCodeAt(0);
    seed = Math.imul(seed, 16777619);
  }
  return seed >>> 0;
}

function randomGenerator(seed) {
  return () => {
    seed += 0x6D2B79F5;
    let value = seed;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function resolveAsset(assetOrSymbol) {
  if (typeof assetOrSymbol === 'object' && assetOrSymbol !== null) return assetOrSymbol;
  return ASSET_MAP.get(assetOrSymbol) || ASSETS[0];
}

function decimalsFor(asset) {
  if (Number.isInteger(asset.priceDecimals) && asset.priceDecimals >= 0 && asset.priceDecimals <= 10) return asset.priceDecimals;
  return asset.symbol === 'EURUSD' ? 5 : asset.symbol === 'USDJPY' ? 3 : 2;
}

function rounded(value, decimals) {
  return Number(value.toFixed(decimals));
}

function interpolate(points, fraction) {
  const index = Math.min(points.length - 2, Math.floor(fraction * (points.length - 1)));
  const local = fraction * (points.length - 1) - index;
  return points[index] * (1 - local) + points[index + 1] * local;
}

export function generateCandles(assetSymbol = 'BTCUSD', timeframe = '1h', count = 240) {
  const asset = resolveAsset(assetSymbol);
  if (!Number.isInteger(count) || count < 1 || count > 20000) throw new RangeError('count must be an integer between 1 and 20000');
  const interval = INTERVALS[timeframe];
  if (!interval) throw new RangeError('Unsupported timeframe');
  const random = randomGenerator(seedFor(`${asset.symbol}:${timeframe}:${count}`));
  const decimals = decimalsFor(asset);
  const tick = 10 ** -decimals;
  const volatility = asset.market === 'Forex' ? 0.32 : asset.market === 'Índices' ? 0.52 : asset.market === 'Ações' ? 0.75 : 1;
  const timeframeScale = timeframe === '1m' ? 0.14 : timeframe === '5m' ? 0.30 : timeframe === '15m' ? 0.53 : timeframe === '4h' ? 1.65 : timeframe === '1D' || timeframe === '1d' ? 2.8 : 1;
  const scale = volatility * timeframeScale;
  const direction = asset.change < 0 ? -0.7 : 1;
  const anchors = [-0.047, -0.035, -0.041, -0.026, -0.029, -0.006, -0.013, 0.004, -0.008, 0.004, 0];
  const raw = [];
  let noise = 0;
  for (let index = 0; index < count; index++) {
    const fraction = count === 1 ? 1 : index / (count - 1);
    noise = noise * 0.65 + (random() - 0.5) * 0.0021;
    const wave = Math.sin(index * 0.63) * 0.0009 + Math.sin(index * 0.17 + 1.2) * 0.0014;
    const trend = interpolate(anchors, fraction) * direction;
    raw.push(asset.price * (1 + (trend + noise + wave) * scale));
  }
  const finalOffset = raw[raw.length - 1] - asset.price;
  const closes = raw.map(value => rounded(value - finalOffset, decimals));
  closes[closes.length - 1] = asset.price;
  let previousClose = rounded(closes[0] * (1 - (random() - 0.35) * 0.002 * scale), decimals);
  const result = [];
  for (let index = 0; index < count; index++) {
    const close = closes[index];
    const open = previousClose;
    const wick = Math.max(tick, asset.price * (0.00018 + random() * 0.00075) * scale);
    const high = rounded(Math.max(open, close) + wick * (0.5 + random()), decimals);
    const low = Math.max(tick, rounded(Math.min(open, close) - wick * (0.5 + random()), decimals));
    const bodyFactor = Math.abs(close - open) / Math.max(asset.price * 0.0008 * scale, tick);
    const baseVolume = asset.market === 'Cripto' ? (asset.price > 10000 ? 62 : 430) : asset.market === 'Forex' ? 1450 : 42000;
    const volume = rounded(baseVolume * (0.35 + random() * 1.3 + bodyFactor * 0.6) * Math.sqrt(interval / 60), 2);
    result.push({ time: END_TIME - (count - 1 - index) * interval, open, high, low, close, volume });
    previousClose = close;
  }
  return result;
}

export function ema(candles, period = 20) {
  if (!Number.isInteger(period) || period < 1) throw new RangeError('period must be a positive integer');
  const alpha = 2 / (period + 1);
  let value;
  return candles.map(candle => {
    const close = typeof candle === 'number' ? candle : candle.close;
    value = value === undefined ? close : close * alpha + value * (1 - alpha);
    return value;
  });
}

export function rsi(candles, period = 14) {
  if (!Number.isInteger(period) || period < 1) throw new RangeError('period must be a positive integer');
  const closes = candles.map(candle => typeof candle === 'number' ? candle : candle.close);
  const result = Array(closes.length).fill(null);
  if (closes.length <= period) return result;
  let gain = 0;
  let loss = 0;
  for (let index = 1; index <= period; index++) {
    const change = closes[index] - closes[index - 1];
    gain += Math.max(0, change) / period;
    loss += Math.max(0, -change) / period;
  }
  const value = () => gain === 0 && loss === 0 ? 50 : loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  result[period] = value();
  for (let index = period + 1; index < closes.length; index++) {
    const change = closes[index] - closes[index - 1];
    gain = (gain * (period - 1) + Math.max(0, change)) / period;
    loss = (loss * (period - 1) + Math.max(0, -change)) / period;
    result[index] = value();
  }
  return result;
}

export function formatPrice(value, assetOrSymbol = 'BTCUSD') {
  if (!Number.isFinite(Number(value))) return '—';
  const asset = resolveAsset(assetOrSymbol);
  const decimals = decimalsFor(asset);
  return Number(value).toLocaleString('pt-BR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function generateOrderBook(assetSymbol = 'BTCUSD', price) {
  const asset = resolveAsset(assetSymbol);
  const mid = Number.isFinite(price) && price > 0 ? price : asset.price;
  const decimals = decimalsFor(asset);
  const tick = 10 ** -decimals;
  const step = Math.max(tick, rounded(mid * 0.000037, decimals));
  const random = randomGenerator(seedFor(`${asset.symbol}:${mid}:book`));
  const sizeBase = asset.market === 'Cripto' ? (mid > 10000 ? 0.15 : 2.5) : asset.market === 'Forex' ? 10000 : 25;
  const build = side => {
    let total = 0;
    return Array.from({ length: 7 }, (_, index) => {
      const size = rounded(sizeBase * (0.15 + random() * 8.5), 4);
      total = rounded(total + size, 4);
      return { price: rounded(mid + side * step * (index + 1), decimals), size, total };
    });
  };
  return { asks: build(1), bids: build(-1) };
}

// Display only: UTC instants, buckets, recordings and exports never depend on this preference.
const DISPLAY_ZONES = new Set(['America/Sao_Paulo', 'UTC', 'America/New_York']);
let displayZone = 'America/Sao_Paulo';
const displayFormatters = new Map();
const displayInstant = value => value === null || value === undefined || value === '' ? NaN : new Date(value).getTime();
function displayFormatter(zone, options) {
  const key = JSON.stringify([zone, options]);
  if (!displayFormatters.has(key)) displayFormatters.set(key, new Intl.DateTimeFormat('en-GB', {timeZone: zone, ...options}));
  return displayFormatters.get(key);
}
function displayParts(value, zone, options) {
  const instant = displayInstant(value);
  return Number.isFinite(instant) ? Object.fromEntries(displayFormatter(zone, options).formatToParts(instant).map(part => [part.type, part.value])) : null;
}
function displayFacade(getZone) {
  const api = {
    getZone,
    zone(value = Date.now()) {
      const zone = getZone(), parts = displayParts(value, zone, {timeZoneName: 'short'});
      if (!parts) return '—';
      if (zone === 'UTC') return 'UTC';
      // Intl supplies the offset/name for this instant, including historical daylight saving.
      const name = parts.timeZoneName.replace('−', '-');
      if (zone === 'America/Sao_Paulo') return ({'GMT-3': 'BRT', 'GMT-2': 'BRST', BRT: 'BRT', BRST: 'BRST'})[name] ?? name;
      return ({'GMT-5': 'EST', 'GMT-4': 'EDT', EST: 'EST', EDT: 'EDT'})[name] ?? name;
    },
    label() { return ({'America/Sao_Paulo': 'BRT/BRST', UTC: 'UTC', 'America/New_York': 'EST/EDT'})[getZone()]; },
    time(value, {seconds = true, milliseconds = false, zone = false} = {}) {
      const parts = displayParts(value, getZone(), {hour: '2-digit', minute: '2-digit', hourCycle: 'h23', ...(seconds || milliseconds ? {second: '2-digit'} : {}), ...(milliseconds ? {fractionalSecondDigits: 3} : {})});
      if (!parts) return '—';
      return `${parts.hour}:${parts.minute}${seconds || milliseconds ? ':' + parts.second : ''}${milliseconds ? '.' + parts.fractionalSecond : ''}${zone ? ' ' + api.zone(value) : ''}`;
    },
    date(value, {year = false} = {}) {
      const parts = displayParts(value, getZone(), {day: '2-digit', month: '2-digit', ...(year ? {year: 'numeric'} : {})});
      return parts ? `${parts.day}/${parts.month}${year ? '/' + parts.year : ''}` : '—';
    },
    dateTime(value, {seconds = true, milliseconds = false, zone = true, year = true} = {}) {
      return Number.isFinite(displayInstant(value)) ? `${api.date(value, {year})} ${api.time(value, {seconds, milliseconds, zone})}` : '—';
    },
    range(from, to, {seconds = true, milliseconds = false, zone = true} = {}) {
      const withDate = api.date(from, {year: true}) !== api.date(to, {year: true});
      const differentZones = zone && api.zone(from) !== api.zone(to);
      const stamp = (value, includeZone) => `${withDate && Number.isFinite(displayInstant(value)) ? api.date(value) + ' ' : ''}${api.time(value, {seconds, milliseconds, zone: includeZone})}`;
      return `${stamp(from, differentZones)}–${stamp(to, zone)}`;
    },
    sessionName(record) {
      if (!record) return 'Sessão';
      const name = String(record.name ?? record.symbol ?? 'Sessão');
      // Only recognize terminal-generated UTC names. User-authored names are left verbatim.
      const numeric = name.match(/^(.* · )(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}) UTC$/);
      const legacy = name.match(/^(.* · )(\d{2})(?: de)? (jan\.?|fev\.?|mar\.?|abr\.?|mai\.?|jun\.?|jul\.?|ago\.?|set\.?|out\.?|nov\.?|dez\.?) (\d{2}):(\d{2}):(\d{2}) UTC$/);
      const match = numeric ?? legacy;
      if (!match || ![record.symbol, record.instrument?.symbol, record.displaySymbol].filter(Boolean).some(symbol => match[1] === `${symbol} · `)) return name;
      let instant, prefix;
      if (numeric) {
        prefix = numeric[1];
        instant = Date.parse(`${numeric[2]}-${numeric[3]}-${numeric[4]}T${numeric[5]}:${numeric[6]}:00Z`);
      } else if (legacy) {
        const references = [record.createdAt, record.updatedAt, record.recording?.from].map(displayInstant).filter(Number.isFinite);
        if (!references.length) return name;
        const years = [...new Set(references.flatMap(value => { const year = new Date(value).getUTCFullYear(); return [year - 1, year, year + 1]; }))];
        const month = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'].indexOf(legacy[3].replace('.', ''));
        prefix = legacy[1];
        const candidates = years.map(year => Date.UTC(year, month, Number(legacy[2]), Number(legacy[4]), Number(legacy[5]), Number(legacy[6])));
        const distance = value => Math.min(...references.map(reference => Math.abs(value - reference)));
        instant = candidates.sort((a, b) => distance(a) - distance(b))[0];
      } else return name;
      return Number.isFinite(instant) ? prefix + api.dateTime(instant) : name;
    }
  };
  return Object.freeze(api);
}
export const displayTime = Object.freeze({
  ...displayFacade(() => displayZone),
  setZone(zone) { displayZone = DISPLAY_ZONES.has(zone) ? zone : 'America/Sao_Paulo'; return displayZone; },
  forZone(zone) { const fixed = DISPLAY_ZONES.has(zone) ? zone : 'America/Sao_Paulo'; return displayFacade(() => fixed); }
});
