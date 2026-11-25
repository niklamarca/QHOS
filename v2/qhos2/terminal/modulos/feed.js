/* Public Coinbase Exchange data, without credentials or synthetic fallbacks.
 * Channel contract: https://docs.cdp.coinbase.com/exchange/websocket-feed/channels
 * REST: https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
 * level2_batch is aggregated Market By Price, not Market By Order. It provides
 * no usable per-update sequence; every reconnection starts from a new snapshot.
 */
export const COINBASE_PRODUCTS = Object.freeze({ BTCUSD: 'BTC-USD', ETHUSD: 'ETH-USD', SOLUSD: 'SOL-USD' });
const SYMBOLS = Object.freeze(Object.fromEntries(Object.entries(COINBASE_PRODUCTS).map(([symbol, product]) => [product, symbol])));
const REST_URL = 'https://api.exchange.coinbase.com';
const WS_URL = 'wss://ws-feed.exchange.coinbase.com';
const FRAME_SECONDS = Object.freeze({ '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1D': 86400 });
const REQUIRED_CHANNELS = ['level2_batch', 'matches', 'heartbeat', 'ticker'];
const TOP_LEVELS = 40;
const BOOK_INTERVAL_MS = 100;
const HEARTBEAT_STALE_MS = 6000;
const HEARTBEAT_TIMEOUT_MS = 12000;

function productFor(symbol) {
  const product = Object.hasOwn(COINBASE_PRODUCTS,symbol) ? COINBASE_PRODUCTS[symbol] : null;
  if (!product) throw new RangeError(`Ativo sem feed Coinbase configurado: ${String(symbol)}`);
  return product;
}

function positive(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function nonnegative(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function serverTime(value) {
  const time = typeof value === 'number' ? value : Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

function tradeId(value) {
  const id = Number(value);
  return Number.isSafeInteger(id) && id >= 0 ? id : null;
}

async function requestJSON(path, { signal, timeout = 12000 } = {}) {
  if (signal?.aborted) throw signal.reason ?? new DOMException('Operação cancelada.', 'AbortError');
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  signal?.addEventListener('abort', abort, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
  const started = performance.now();
  try {
    const response = await fetch(`${REST_URL}${path}`, { signal: controller.signal, cache: 'no-store', credentials: 'omit' });
    if (!response.ok) {
      let detail = '';
      try { detail = String((await response.json())?.message ?? '').slice(0,180); } catch {}
      const error = new Error(`Coinbase HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
      error.status = response.status;
      error.retryAfter = nonnegative(response.headers.get('retry-after'));
      throw error;
    }
    const data = await response.json();
    return { data, rtt: Math.round(performance.now() - started), receivedAt: Date.now(), exchangeTime: serverTime(response.headers.get('date')) };
  } catch (error) {
    if (timedOut && !signal?.aborted) throw new Error('Tempo limite da API Coinbase excedido.');
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}

function parseTrade(row, symbol, source, receivedAt = Date.now()) {
  const id = tradeId(row.trade_id), price = positive(row.price), size = positive(row.size), time = serverTime(row.time);
  if (id === null || price === null || size === null || time === null || !['buy', 'sell'].includes(row.side)) return null;
  return { symbol, id, time, price, size, side: row.side === 'sell' ? 'buy' : 'sell', makerSide: row.side, source, receivedAt };
}

export async function fetchTrades(symbol, { signal, limit = 100 } = {}) {
  const product = productFor(symbol);
  const count = Math.max(1, Math.min(100, Math.floor(Number(limit) || 100)));
  const response = await requestJSON(`/products/${product}/trades?limit=${count}`, { signal });
  if (!Array.isArray(response.data)) throw new Error('Resposta de negócios da Coinbase inválida.');
  const unique = new Map();
  for (const row of response.data) {
    const trade = parseTrade(row, symbol, 'backfill', response.receivedAt);
    if (trade) unique.set(trade.id, trade);
  }
  return [...unique.values()].sort((a,b) => a.id - b.id);
}

export async function fetchProductStats(symbol, { signal } = {}) {
  const product = productFor(symbol);
  const response = await requestJSON(`/products/${product}/stats`, { signal });
  const row = response.data;
  if (!row || positive(row.last) === null) throw new Error('Estatísticas da Coinbase inválidas.');
  return {
    symbol, product, price: positive(row.last), open: positive(row.open), high: positive(row.high), low: positive(row.low),
    volume: nonnegative(row.volume), volume30day: nonnegative(row.volume_30day), restRtt: response.rtt,
    time: response.exchangeTime, receivedAt: response.receivedAt,
  };
}

export async function loadCandles(symbol, timeframe, { signal } = {}) {
  const product = productFor(symbol), duration = FRAME_SECONDS[timeframe];
  if (!duration) throw new RangeError(`Período não suportado: ${String(timeframe)}`);
  const granularity = timeframe === '4h' ? 3600 : duration;
  const now = Math.floor(Date.now() / 1000);
  const firstBucket = Math.floor(now / duration) * duration - 239 * duration;
  const unique = new Map();
  // Explicit windows contain at most 300 buckets, including both endpoints.
  // Four 1h requests provide enough observations for up to 240 aggregated 4h bars.
  for (let start = firstBucket; start < now; start += 299 * granularity) {
    const end = Math.min(now, start + 299 * granularity);
    const query = new URLSearchParams({ granularity: String(granularity), start: new Date(start * 1000).toISOString(), end: new Date(end * 1000).toISOString() });
    const response = await requestJSON(`/products/${product}/candles?${query}`, { signal });
    if (!Array.isArray(response.data)) throw new Error('Resposta de candles da Coinbase inválida.');
    for (const row of response.data) {
      if (!Array.isArray(row) || row.length < 6) continue;
      const [time, low, high, open, close, volume] = row.map(Number);
      if (![time,low,high,open,close,volume].every(Number.isFinite) || time < firstBucket || time > now || low <= 0 || high < low || high < Math.max(open,close) || low > Math.min(open,close) || volume < 0) continue;
      unique.set(time, { time, open, high, low, close, volume, partial: time + granularity > now, source: 'coinbase' });
    }
  }
  const sorted = [...unique.values()].sort((a,b) => a.time-b.time);
  if (!sorted.length) throw new Error('A Coinbase não retornou candles para este período.');
  if (timeframe !== '4h') return sorted.slice(-240);
  const groups = new Map();
  for (const bar of sorted) {
    const time = Math.floor(bar.time / duration) * duration;
    const group = groups.get(time);
    if (!group) groups.set(time, { time, open:bar.open, high:bar.high, low:bar.low, close:bar.close, volume:bar.volume, observations:1, partial:bar.partial, source:'coinbase' });
    else { group.high=Math.max(group.high,bar.high); group.low=Math.min(group.low,bar.low); group.close=bar.close; group.volume+=bar.volume; group.observations++; group.partial ||= bar.partial; }
  }
  return [...groups.values()].map(bar => ({ ...bar, partial: bar.partial || bar.observations < 4 || bar.time + duration > now })).slice(-240);
}

/**
 * Callback times: trade.time/ticker.time are exchange timestamps (milliseconds).
 * book.time and status.last*At are local arrival times. book.exchangeTime and
 * lastTradeTime/lastHeartbeatTime retain exchange timestamps when supplied.
 * gaps counts observed gap events; missing/recovered/unrecovered count trade IDs.
 * A recovered gap remains recorded. Recovery is limited to the latest 100 trades.
 */
export function createFeed({ onStatus, onTicker, onTrade, onBook, onGap } = {}) {
  let symbol = null, product = null, stopped = true, generation = 0, socket = null, controller = null;
  let tickerProducts = Object.values(COINBASE_PRODUCTS);
  let reconnectTimer = null, watchdogTimer = null, bookTimer = null, heartbeatGapTimer = null, recoveryTimer = null;
  let connectionStarted = 0, lastBookEmit = 0, retryAttempt = 0, liveHeartbeats = 0, lastRecoveryStarted = 0;
  let bids = new Map(), asks = new Map(), bookReady = false, bookAt = null, bookExchangeTime = null;
  let highWater = null, heartbeatHighWater = null, missingRanges = [], recoveryInFlight = false, recoverAgain = false;
  let listenersAttached = false;
  let status = freshStatus();

  function freshStatus() {
    return { state:'offline', symbol:null, product:null, lastMessageAt:null, lastTradeAt:null, lastHeartbeatAt:null,
      lastTradeTime:null, lastHeartbeatTime:null, lastBookAt:null, reconnects:0, gaps:0, missing:0, recovered:0,
      unrecovered:0, startedAt:null, restRtt:null, error:null, recoveryError:null, recovering:false,
      bookReady:false, subscriptionConfirmed:false, bookSequenceVerified:false, tradesReceived:0 };
  }

  function invoke(callback, value) {
    if (typeof callback !== 'function') return;
    try { callback(value); } catch (error) { console.error('QHOS feed callback:', error); }
  }

  function emitStatus(changes = {}) {
    Object.assign(status, changes, { symbol, product, bookReady });
    invoke(onStatus, { ...status });
  }

  function bookView() {
    return { symbol, product, bids:[...bids].sort((a,b) => b[0]-a[0]).slice(0,TOP_LEVELS),
      asks:[...asks].sort((a,b) => a[0]-b[0]).slice(0,TOP_LEVELS), time:bookAt, receivedAt:bookAt,
      exchangeTime:bookExchangeTime, ready:bookReady, sequenceVerified:false };
  }

  function emitBook(immediate = false) {
    if (bookTimer !== null && !immediate) return;
    if (immediate) { clearTimeout(bookTimer); bookTimer=null; }
    const elapsed = Date.now() - lastBookEmit;
    if (!immediate && elapsed < BOOK_INTERVAL_MS) {
      const ownGeneration = generation;
      bookTimer=setTimeout(() => { bookTimer=null; if (ownGeneration===generation && !stopped) emitBook(true); },BOOK_INTERVAL_MS-elapsed);
      return;
    }
    lastBookEmit=Date.now();invoke(onBook,bookView());
  }

  function clearBook() {
    bids=new Map();asks=new Map();bookReady=false;bookAt=null;bookExchangeTime=null;status.lastBookAt=null;
    emitBook(true);
  }

  function clearConnection() {
    generation++;
    for (const timer of [reconnectTimer,bookTimer,heartbeatGapTimer,recoveryTimer]) clearTimeout(timer);
    clearInterval(watchdogTimer);
    reconnectTimer=bookTimer=heartbeatGapTimer=recoveryTimer=watchdogTimer=null;
    controller?.abort();controller=null;recoveryInFlight=false;recoverAgain=false;
    if (socket) { const old=socket;socket=null;old.onopen=old.onmessage=old.onerror=old.onclose=null;try { old.close(1000,'QHOS reconnection'); } catch {} }
    status.recovering=false;status.subscriptionConfirmed=false;
  }

  function emitGap(detail) {
    invoke(onGap, { symbol, product, time:Date.now(), gaps:status.gaps, missing:status.missing,
      recovered:status.recovered, unrecovered:status.unrecovered, ...detail });
  }

  function addMissing(from, to, reason) {
    if (from>to) return;
    const count=to-from+1;
    const previous=missingRanges.at(-1);
    if (previous && previous.to+1===from) previous.to=to;
    else missingRanges.push({from,to});
    // Old unrecovered IDs remain in the public counter even if their ranges are
    // retired to bound browser memory; latest-100 recovery cannot reach them.
    if (missingRanges.length>2048) missingRanges.shift();
    status.gaps++;status.missing+=count;status.unrecovered+=count;
    emitGap({ type:'detected', reason, from, to, count });
    emitStatus();scheduleRecovery();
  }

  function recoverId(id) {
    const index=missingRanges.findIndex(range => id>=range.from && id<=range.to);
    if (index<0) return false;
    const range=missingRanges[index];
    if (range.from===range.to) missingRanges.splice(index,1);
    else if (id===range.from) range.from++;
    else if (id===range.to) range.to--;
    else missingRanges.splice(index,1,{from:range.from,to:id-1},{from:id+1,to:range.to});
    status.recovered++;status.unrecovered--;return true;
  }

  function acceptTrade(trade) {
    let recovered=false;
    if (highWater===null) highWater=trade.id;
    else if (trade.id>highWater) {
      if (trade.id>highWater+1) addMissing(highWater+1,trade.id-1,'trade-id');
      highWater=trade.id;
    } else {
      recovered=recoverId(trade.id);
      if (!recovered) return false; // repeated, historical, or already recovered ID
    }
    status.tradesReceived++;
    if (trade.source==='live') { status.lastTradeAt=trade.receivedAt;status.lastTradeTime=trade.time; }
    invoke(onTrade,trade);
    if (recovered && trade.source==='live') { emitGap({type:'recovered',source:'live',count:1,id:trade.id});emitStatus(); }
    return true;
  }

  function observeHeartbeatId(id) {
    if (id===null) return;
    if (highWater===null) { highWater=id;return; }
    heartbeatHighWater=Math.max(heartbeatHighWater??0,id);
    if (heartbeatGapTimer!==null || heartbeatHighWater<=highWater) return;
    const ownGeneration=generation;
    heartbeatGapTimer=setTimeout(() => {
      heartbeatGapTimer=null;
      if (ownGeneration!==generation || stopped || heartbeatHighWater<=highWater) return;
      const from=highWater+1,to=heartbeatHighWater;highWater=to;
      addMissing(from,to,'heartbeat');
    },500);
  }

  function scheduleRecovery() {
    if (stopped || !missingRanges.length) return;
    if (recoveryInFlight) {recoverAgain=true;return;}
    if (recoveryTimer!==null) return;
    const ownGeneration=generation;
    const wait=Math.max(180,1500-(Date.now()-lastRecoveryStarted));
    recoveryTimer=setTimeout(() => {
      recoveryTimer=null;
      if (ownGeneration===generation && !stopped) void recoverRecent(ownGeneration);
    },wait);
  }

  async function recoverRecent(ownGeneration) {
    if (recoveryInFlight || stopped || ownGeneration!==generation || !missingRanges.length) return;
    recoveryInFlight=true;lastRecoveryStarted=Date.now();const before=status.recovered;
    emitStatus({recovering:true});
    try {
      const response=await requestJSON(`/products/${product}/trades?limit=100`,{signal:controller.signal});
      if (stopped || ownGeneration!==generation) return;
      if (!Array.isArray(response.data)) throw new Error('Resposta de recuperação de negócios inválida.');
      const unique=new Map();
      for(const row of response.data) {const trade=parseTrade(row,symbol,'backfill',response.receivedAt);if(trade)unique.set(trade.id,trade);}
      for(const trade of [...unique.values()].sort((a,b)=>a.id-b.id)) acceptTrade(trade);
      status.restRtt=response.rtt;status.recoveryError=null;
      emitGap({type:'recovery',source:'backfill',count:status.recovered-before,window:100,remaining:status.unrecovered});
    } catch(error) {
      if(stopped || ownGeneration!==generation || error.name==='AbortError') return;
      status.recoveryError=String(error.message||error);
      emitGap({type:'recovery-failed',window:100,remaining:status.unrecovered,error:status.recoveryError});
    } finally {
      if(ownGeneration===generation && !stopped) {
        recoveryInFlight=false;emitStatus({recovering:false});
        if(recoverAgain) {recoverAgain=false;scheduleRecovery();}
      }
    }
  }

  function queueReconnect(error, immediate=false) {
    if(stopped || reconnectTimer!==null) return;
    clearConnection();clearBook();
    const ownGeneration=generation;
    const offline=globalThis.navigator?.onLine===false;
    emitStatus({state:offline?'offline':'reconnecting',error:String(error||'Reconectando ao feed público.')});
    const wait=immediate?0:Math.min(30000,1000*2**Math.min(retryAttempt++,5))*(0.85+Math.random()*0.3);
    reconnectTimer=setTimeout(() => {
      reconnectTimer=null;
      if(stopped || ownGeneration!==generation) return;
      if(globalThis.navigator?.onLine===false) {queueReconnect('Navegador sem conexão de rede.');return;}
      status.reconnects++;connect(true);
    },wait);
  }

  function reconcileHealth() {
    if(stopped) return;
    const now=Date.now();
    const heartbeatAge=now-(status.lastHeartbeatAt??connectionStarted);
    if(globalThis.navigator?.onLine===false) {queueReconnect('Navegador sem conexão de rede.');return;}
    if(!status.subscriptionConfirmed && now-connectionStarted>10000) {queueReconnect('A Coinbase não confirmou a assinatura em 10 s.');return;}
    if(!bookReady && now-connectionStarted>15000) {queueReconnect('O snapshot do book não chegou em 15 s.');return;}
    if(heartbeatAge>HEARTBEAT_TIMEOUT_MS) {queueReconnect('Heartbeat da Coinbase ausente por mais de 12 s.');return;}
    if(heartbeatAge>HEARTBEAT_STALE_MS) {
      if(status.state!=='stale') emitStatus({state:'stale',error:'Heartbeat atrasado. Dados podem estar desatualizados.'});
    } else if(bookReady && status.subscriptionConfirmed && status.lastHeartbeatAt) {
      if(status.state!=='live' || status.error) emitStatus({state:'live',error:null});
    }
  }

  function updateMap(map,priceValue,sizeValue) {
    const price=positive(priceValue),size=nonnegative(sizeValue);
    if(price===null || size===null) return false;
    if(size===0) map.delete(price);else map.set(price,size);
    return true;
  }

  function readSnapshot(message,receivedAt) {
    if(!Array.isArray(message.bids) || !Array.isArray(message.asks)) {queueReconnect('Snapshot do book inválido.');return;}
    const nextBids=new Map(),nextAsks=new Map();
    for(const row of message.bids) if(!Array.isArray(row) || !updateMap(nextBids,row[0],row[1])) {queueReconnect('Nível inválido no snapshot do book.');return;}
    for(const row of message.asks) if(!Array.isArray(row) || !updateMap(nextAsks,row[0],row[1])) {queueReconnect('Nível inválido no snapshot do book.');return;}
    if(!nextBids.size || !nextAsks.size) {queueReconnect('Snapshot do book vazio.');return;}
    bids=nextBids;asks=nextAsks;bookReady=true;bookAt=receivedAt;bookExchangeTime=serverTime(message.time);status.lastBookAt=receivedAt;
    emitBook(true);reconcileHealth();emitStatus();
  }

  function readBookUpdate(message,receivedAt) {
    if(!bookReady) return; // never combine an update with an old connection's book
    if(!Array.isArray(message.changes)) {queueReconnect('Atualização do book inválida.');return;}
    for(const change of message.changes) {
      if(!Array.isArray(change) || !['buy','sell'].includes(change[0]) || !updateMap(change[0]==='buy'?bids:asks,change[1],change[2])) {
        queueReconnect('Nível inválido no book; solicitando novo snapshot.');return;
      }
    }
    bookAt=receivedAt;bookExchangeTime=serverTime(message.time);status.lastBookAt=receivedAt;emitBook();
  }

  function readTicker(message,receivedAt) {
    const tickerSymbol=SYMBOLS[message.product_id],price=positive(message.price);
    if(!tickerSymbol || price===null) return;
    invoke(onTicker,{symbol:tickerSymbol,product:message.product_id,price,bid:positive(message.best_bid),ask:positive(message.best_ask),
      volume:nonnegative(message.volume_24h),open:positive(message.open_24h),high:positive(message.high_24h),low:positive(message.low_24h),
      time:serverTime(message.time),receivedAt});
  }

  function acknowledge(message) {
    if(!Array.isArray(message.channels)) {queueReconnect('Confirmação de assinatura inválida.');return;}
    const confirmed=new Set();
    for(const channel of message.channels) {
      const name=['level2_50','level2_batch','level2'].includes(channel.name)?'level2_batch':channel.name;
      if(Array.isArray(channel.product_ids) && channel.product_ids.includes(product)) confirmed.add(name);
    }
    const missing=REQUIRED_CHANNELS.filter(name=>!confirmed.has(name));
    if(missing.length) {queueReconnect(`Canais não confirmados: ${missing.join(', ')}.`);return;}
    status.subscriptionConfirmed=true;emitStatus();reconcileHealth();
  }

  function connect(isReconnect=false) {
    if(stopped || !symbol) return;
    clearConnection();clearBook();
    const ownGeneration=generation;
    controller=new AbortController();connectionStarted=Date.now();liveHeartbeats=0;
    status.lastHeartbeatAt=null;status.lastBookAt=null;status.subscriptionConfirmed=false;
    emitStatus({state:isReconnect?'reconnecting':'connecting',error:null});
    try {socket=new WebSocket(WS_URL);} catch(error) {queueReconnect(error.message);return;}
    const currentSocket=socket;
    const current=()=>!stopped && ownGeneration===generation && socket===currentSocket;
    currentSocket.onopen=()=>{
      if(!current()) return;
      try {
        currentSocket.send(JSON.stringify({type:'subscribe',channels:[
          {name:'level2_batch',product_ids:[product]}, {name:'matches',product_ids:[product]},
          {name:'heartbeat',product_ids:[product]}, {name:'ticker',product_ids:tickerProducts},
        ]}));
      } catch(error) {queueReconnect(error.message);}
    };
    currentSocket.onmessage=event=>{
      if(!current()) return;
      const receivedAt=Date.now();let message;
      try {message=JSON.parse(event.data);} catch {queueReconnect('Mensagem inválida recebida da Coinbase.');return;}
      if(!message || typeof message!=='object') return;
      status.lastMessageAt=receivedAt;
      if(message.type==='error') {queueReconnect(String(message.message||'A Coinbase rejeitou a assinatura.'));return;}
      if(message.type==='subscriptions') {acknowledge(message);return;}
      if(message.type==='ticker') {readTicker(message,receivedAt);return;}
      if(message.product_id!==product) return;
      if(message.type==='snapshot') readSnapshot(message,receivedAt);
      else if(message.type==='l2update') readBookUpdate(message,receivedAt);
      else if(message.type==='match' || message.type==='last_match') {
        const trade=parseTrade(message,symbol,message.type==='last_match'?'backfill':'live',receivedAt);
        if(trade) acceptTrade(trade);
      } else if(message.type==='heartbeat') {
        status.lastHeartbeatAt=receivedAt;status.lastHeartbeatTime=serverTime(message.time);
        observeHeartbeatId(tradeId(message.last_trade_id));
        if(++liveHeartbeats>=3) retryAttempt=0;
        reconcileHealth();emitStatus();
      }
    };
    currentSocket.onerror=()=>{if(current())queueReconnect('Falha na conexão WebSocket com a Coinbase.');};
    currentSocket.onclose=event=>{if(current())queueReconnect(`Conexão Coinbase encerrada (código ${event.code}).`);};
    watchdogTimer=setInterval(reconcileHealth,1000);
    if(missingRanges.length) scheduleRecovery();
  }

  const online=()=>{if(!stopped)queueReconnect('Conexão de rede restabelecida.',true);};
  const offline=()=>{if(!stopped)queueReconnect('Navegador sem conexão de rede.');};
  const visible=()=>{
    if(!stopped && globalThis.document?.visibilityState==='visible' && Date.now()-(status.lastHeartbeatAt??connectionStarted)>HEARTBEAT_STALE_MS) queueReconnect('Ressincronizando após pausa do navegador.',true);
  };
  function attachListeners() {
    if(listenersAttached) return;
    globalThis.addEventListener?.('online',online);globalThis.addEventListener?.('offline',offline);globalThis.document?.addEventListener('visibilitychange',visible);listenersAttached=true;
  }
  function detachListeners() {
    if(!listenersAttached) return;
    globalThis.removeEventListener?.('online',online);globalThis.removeEventListener?.('offline',offline);globalThis.document?.removeEventListener('visibilitychange',visible);listenersAttached=false;
  }

  return {
    start(nextSymbol, {symbols = Object.keys(COINBASE_PRODUCTS)} = {}) {
      const nextProduct=productFor(nextSymbol);
      const nextTickers = [...new Set([...symbols, nextSymbol])].map(productFor);
      stopped=true;clearConnection();
      tickerProducts=nextTickers;
      symbol=nextSymbol;product=nextProduct;stopped=false;status=freshStatus();status.startedAt=Date.now();
      highWater=null;heartbeatHighWater=null;missingRanges=[];retryAttempt=0;lastRecoveryStarted=0;
      attachListeners();connect();return this;
    },
    stop() {
      stopped=true;clearConnection();detachListeners();clearBook();emitStatus({state:'offline',error:null});
    },
    reconnect() {
      if(!symbol) return;
      if(stopped) {stopped=false;attachListeners();}
      if(reconnectTimer!==null) {clearTimeout(reconnectTimer);reconnectTimer=null;}
      queueReconnect('Reconexão solicitada.',true);
    },
    getBook:bookView,
    getStatus:()=>({...status,symbol,product,bookReady}),
  };
}
