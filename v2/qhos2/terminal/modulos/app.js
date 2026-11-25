import {ASSETS, ema, formatPrice as rawFormatPrice, displayTime} from 'qhos-module/market.js';
import {TerminalChart} from 'qhos-module/chart.js';
import {ExecutionLayer} from 'qhos-module/execution-ui.js';
import {MicrostructurePanel} from 'qhos-module/microstructure-ui.js';
import {ScreenNavigation} from 'qhos-module/navigation.js';
import {icon, hydrateIcons} from 'qhos-module/icons.js';
import {createFeed, loadCandles, COINBASE_PRODUCTS} from 'qhos-module/feed.js';
import {createQhosBridge, normalizeInstrument, historyCoverageNotice} from 'qhos-module/qhos-bridge.js';
import {ContractManagerUI} from 'qhos-module/contract-ui.js';
import {createFlowStore, simulateMarketFill} from 'qhos-module/flow.js';
import {computeCVD, computeHeatmap, FlowCanvas} from 'qhos-module/flow-visuals.js';
import {createSession, parseSession, listSessions, saveSession, getSession, deleteSession, renameSession} from 'qhos-module/sessions.js';
import {DEFAULT_FEE_BPS, feeFor, validateBrackets, getExitSignal, computeExit, entryCapital} from 'qhos-module/risk.js';

// Market data sources are isolated. Trading remains the local Coinbase spot simulator.
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
const esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(n,d=2)=>Number.isFinite(n)?n.toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
const quantity=n=>Number.isFinite(n)?num(n,Number.isInteger(n)?0:n!==0&&Math.abs(n)<0.0001?8:Math.abs(n)<0.01?6:4):'—';
const compact=n=>Number.isFinite(n)?new Intl.NumberFormat('pt-BR',{notation:'compact',maximumFractionDigits:2}).format(n):'—';
const formatPrice=(v,a)=>Number.isFinite(v)?rawFormatPrice(v,a):'—';
const money=n=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'USD'}).format(n);
const signedMoney=n=>`${n>=0?'+':'−'}${money(Math.abs(n))}`;
const percent=n=>Number.isFinite(n)?`${n>=0?'+':''}${num(n)}%`:'—';
const utcDisplay=displayTime.forZone('UTC');
const expiryText=value=>String(value??'').replace(/^(\d{4})-(\d{2})-(\d{2})$/,'$3/$2/$1');
const displayStamp=(time,kind)=>kind==='dateTime'?displayTime.dateTime(time):displayTime.time(time,{zone:kind==='zoned'});
const timeMarkup=(time,kind='time')=>`<span data-display-instant="${Number.isFinite(time)?time:''}" data-display-kind="${kind}" title="${esc(displayTime.dateTime(time))}">${esc(displayStamp(time,kind))}</span>`;
const rangeMarkup=(from,to)=>`<span data-display-from="${Number.isFinite(from)?from:''}" data-display-to="${Number.isFinite(to)?to:''}">${esc(displayTime.range(from,to))}</span>`;
function refreshDisplayTimes(){
  $$('[data-display-instant]').forEach(el=>{const time=el.dataset.displayInstant===''?null:Number(el.dataset.displayInstant);el.textContent=displayStamp(time,el.dataset.displayKind);el.title=displayTime.dateTime(time);});
  $$('[data-display-from]').forEach(el=>{const from=el.dataset.displayFrom===''?null:Number(el.dataset.displayFrom),to=el.dataset.displayTo===''?null:Number(el.dataset.displayTo);el.textContent=displayTime.range(from,to);});
  $$('[data-display-zone]').forEach(el=>{el.textContent=displayTime.label();});
}
const ageText=t=>t?`${num(Math.max(0,Date.now()-t)/1000,1)} s`:'—';
const load=(k,f)=>{try{return JSON.parse(localStorage.getItem(`qhos-v2-${k}`))??f;}catch{return f;}};
const save=(k,v)=>{try{localStorage.setItem(`qhos-v2-${k}`,JSON.stringify(v));}catch{toast('Não foi possível salvar neste navegador.','error');}};
const coinbaseAssets=ASSETS.filter(a=>Object.hasOwn(COINBASE_PRODUCTS,a.symbol)).map(a=>({...a,id:a.symbol,displaySymbol:a.symbol,provider:'coinbase',assetClass:'spot',quantityUnit:a.symbol.replace('USD',''),tickSize:.01,priceDecimals:2,contractMultiplier:1,capabilities:{quotes:true,trades:true,depth:true,history:true},price:null,change:null,bid:null,ask:null,open:null,volume:null,exchange:'COINBASE',sourceLabel:'Coinbase Exchange',updatedAt:0}));
const assetMap=new Map(coinbaseAssets.map(a=>[a.symbol,a]));
const savedSource=load('source-v4',{});
let coinbaseIds=Array.isArray(savedSource.coinbaseIds)?[...new Set(savedSource.coinbaseIds)].filter(id=>assetMap.has(id)).slice(0,3):coinbaseAssets.map(a=>a.id);
if(!coinbaseIds.length)coinbaseIds=coinbaseAssets.map(a=>a.id);
let currentProvider='coinbase',qhosEndpoint=typeof savedSource.endpoint==='string'?savedSource.endpoint:'',qhosEnvironment=null,contractManager=null,collectorAuto=null,autoConnectDispatch=false;
const assets=coinbaseIds.map(id=>assetMap.get(id));
if(savedSource.provider==='rithmic'&&Array.isArray(savedSource.instruments)){
  try{const restored=savedSource.instruments.map(normalizeInstrument);if(!restored.length||restored.length>3||new Set(restored.map(a=>a.id)).size!==restored.length||!restored.some(a=>a.id===savedSource.activeId))throw new Error('Seleção salva inválida.');qhosEnvironment=['test','paper','synthetic','unknown','production'].includes(savedSource.environment)?savedSource.environment:null;assets.splice(0,assets.length,...restored.map(registerInstrument));currentProvider='rithmic';}catch{/* A malformed local preference cannot become a confirmed market subscription. */}
}
const prefs=load('preferences',{}),validFrames=['1m','5m','15m','1h','4h','1D'];
displayTime.setZone(prefs.timeZone);
const frameSeconds={'1m':60,'5m':300,'15m':900,'1h':3600,'4h':14400,'1D':86400};
const rawWorkspace=load('workspace-v3',{});
const workspace={density:rawWorkspace.density==='comfortable'?'comfortable':'compact',mode:['balanced','flow','focus'].includes(rawWorkspace.mode)?rawWorkspace.mode:'balanced',panelHeight:Math.max(140,Math.min(520,Number(rawWorkspace.panelHeight)||320)),contrast:!!rawWorkspace.contrast,autoSave:rawWorkspace.autoSave!==false,feeBps:Number.isFinite(rawWorkspace.feeBps)&&rawWorkspace.feeBps>=0&&rawWorkspace.feeBps<=1000?rawWorkspace.feeBps:DEFAULT_FEE_BPS};
const rawFlowPrefs=load('flow-v3',{});
const flowPrefs={bucketSeconds:[15,30,60,300].includes(rawFlowPrefs.bucketSeconds)?rawFlowPrefs.bucketSeconds:60,columns:[3,6,9,12].includes(rawFlowPrefs.columns)?rawFlowPrefs.columns:6,rows:[8,12,20,40].includes(rawFlowPrefs.rows)?rawFlowPrefs.rows:12,minSize:Math.max(0,Math.min(1e6,Number(rawFlowPrefs.minSize)||0)),imbalance:[0,2,3,4].includes(rawFlowPrefs.imbalance)?rawFlowPrefs.imbalance:3,steps:{BTCUSD:10,ETHUSD:1,SOLUSD:.1,...rawFlowPrefs.steps},cvdBucket:[1,5,15,60].includes(rawFlowPrefs.cvdBucket)?rawFlowPrefs.cvdBucket:5,cvdWindow:[60,180,600,0].includes(rawFlowPrefs.cvdWindow)?rawFlowPrefs.cvdWindow:180,cvdBaseline:rawFlowPrefs.cvdBaseline==='window'?'window':'session',heatWindow:[60,180,600].includes(rawFlowPrefs.heatWindow)?rawFlowPrefs.heatWindow:180,heatSteps:{BTCUSD:1,ETHUSD:.1,SOLUSD:.01,...rawFlowPrefs.heatSteps}};
let currentSymbol=currentProvider==='rithmic'?savedSource.activeId:coinbaseIds.includes(prefs?.symbol)?prefs.symbol:coinbaseIds[0];
let currentTimeframe=validFrames.includes(prefs?.timeframe)?prefs.timeframe:'5m';
let chartOptions={type:'candles',ema20:true,ema50:true,volume:true,rsi:true,grid:true};
if(prefs?.options){for(const k of ['ema20','ema50','volume','rsi','grid'])if(typeof prefs.options[k]==='boolean')chartOptions[k]=prefs.options[k];if(['candles','line','area'].includes(prefs.options.type))chartOptions.type=prefs.options.type;}
let favorites=assets.map(a=>a.symbol),sortDirection=0,tradeTab='positions',selectedTool='crosshair',analysisTab='footprint',flowTab='dom';
let candles=[],historyCandles=[],captureTrades=[],captureIds=new Set(),liveBuckets=new Map(),historyBoundary=0,historyVersion=0,historyAbort=null,historyError='',historyUpdatedAt=0,historyCoverage=null;
let historyPendingVersion=0,historyTradeVersion=0,rithmicHistoryCoveredVersion=0,rithmicHistoryAttemptAt=0;
let activeModal='',lastFocused=null,refreshOrderForm=null,renderDirty=true,chartDirty=true;
let replayTimer=null,replayPaused=false,replayTime=0,replayFrom=0,replayTo=0,replaySpeed=1,replayData=null,replayFlow=null,replayAsset=null;
let feedStatus={state:currentProvider==='rithmic'?'offline':'connecting',gaps:0,recovered:0,unrecovered:0,reconnects:0};
let liveBook={bids:[],asks:[],ready:false,time:0};
let captureVersion=0,analysisCache=null,analysisCacheVersion=-1,sessionCatalog=[],sessionBusy=false,lastSavedAt=0,storageError='',captureGaps=[],activeGap=null;
let captureSessionId=globalThis.crypto?.randomUUID?.()??`capture-${Date.now()}`,captureCreatedAt=Date.now(),captureName=sessionName(),lastSavedVersion=-1;
let cvdView=null,heatmapView=null,footprintOffset=0,flowInspect=null;
let chartDrawingSymbol='',contextPoint=null;
let executionLayer=null,paperExecutionVersion=0,executionContextKey='';
const rawDrawings=load('drawings-v3',{}),savedDrawings=rawDrawings&&typeof rawDrawings==='object'&&!Array.isArray(rawDrawings)?rawDrawings:{};
const flow=createFlowStore();flow.reset(currentSymbol,sourceMetadata(assetMap.get(currentSymbol)));
const rawAccount=load('account',{});
const validTrade=t=>t&&assetMap.has(t.symbol)&&['buy','sell'].includes(t.side)&&Number.isFinite(t.qty)&&t.qty>0&&Number.isFinite(t.price)&&t.price>0&&typeof t.id==='string';
const migrateRisk=p=>({...p,feeBps:Number.isFinite(p.feeBps)&&p.feeBps>=0&&p.feeBps<=1000?p.feeBps:workspace.feeBps,entryFeeRemaining:Number.isFinite(p.entryFeeRemaining)&&p.entryFeeRemaining>=0?p.entryFeeRemaining:0,stopPrice:Number.isFinite(p.stopPrice)&&p.stopPrice>0?p.stopPrice:null,targetPrice:Number.isFinite(p.targetPrice)&&p.targetPrice>0?p.targetPrice:null,stopActive:!!p.stopActive});
const account={balance:Number.isFinite(rawAccount.balance)?rawAccount.balance:100000,realized:Number.isFinite(rawAccount.realized)?rawAccount.realized:0,feesPaid:Number.isFinite(rawAccount.feesPaid)&&rawAccount.feesPaid>=0?rawAccount.feesPaid:0,positions:Array.isArray(rawAccount.positions)?rawAccount.positions.filter(validTrade).map(migrateRisk):[],orders:Array.isArray(rawAccount.orders)?rawAccount.orders.filter(validTrade).map(migrateRisk):[],history:Array.isArray(rawAccount.history)?rawAccount.history.filter(validTrade).slice(0,200):[]};
const rawAlerts=load('alerts',[]);
let alerts=Array.isArray(rawAlerts)?rawAlerts.filter(a=>a&&typeof a.symbol==='string'&&a.symbol.length<=160&&/^[A-Za-z0-9][A-Za-z0-9_.:/-]*$/.test(a.symbol)&&Number.isFinite(a.price)&&a.price>0&&['above','below'].includes(a.condition)):[];
const viewSymbol=()=>replayTimer?replayData.symbol:currentSymbol;
const currentAsset=()=>replayTimer?replayAsset:assetMap.get(currentSymbol),unit=()=>currentAsset()?.quantityUnit??'unidades';
const displaySymbol=(symbol=viewSymbol())=>(replayTimer&&symbol===replayData.symbol?replayAsset:assetMap.get(symbol))?.displaySymbol??symbol;
const currency=()=>currentAsset()?.currency??'USD';
const sourceLabel=()=>replayTimer?(replayData.imported?'Arquivo importado':replayData.sourceLabel??replayData.source??currentAsset()?.sourceLabel):currentAsset()?.sourceLabel??'Fonte indisponível';
const paperSupported=()=>!replayTimer&&currentProvider==='coinbase'&&currentAsset()?.provider==='coinbase';
const exposureUSD=(s,q,p)=>q*p;
const markPrice=p=>{const a=assetMap.get(p.symbol);return (p.side==='buy'?a?.bid:a?.ask)??a?.price??p.price;};
const positionPnl=p=>(markPrice(p)-p.price)*p.qty*(p.side==='buy'?1:-1);
const totalPnl=()=>account.positions.reduce((s,p)=>s+positionPnl(p),0);
const marginUsed=()=>account.positions.reduce((s,p)=>s+p.qty*p.price,0);
const orderReserve=p=>p.qty*p.price+feeFor(p.qty*p.price,p.feeBps);
const reservedMargin=()=>account.orders.reduce((s,p)=>s+orderReserve(p),0);
const freeEquity=()=>account.balance+totalPnl()-marginUsed()-reservedMargin();
const availableBalance=()=>Math.max(0,freeEquity());
const persistAccount=()=>{save('account',account);paperExecutionVersion++;chartDirty=true;};
const persistPrefs=()=>save('preferences',{symbol:currentSymbol,timeframe:currentTimeframe,options:chartOptions,timeZone:displayTime.getZone()});
const uid=()=>globalThis.crypto?.randomUUID?.()??`${Date.now()}-${Math.random().toString(36).slice(2)}`;
const badge=a=>`<span class="asset-icon" style="background:${a.color}">${esc(a.icon)}</span>`;
const healthyBook=()=>paperSupported()&&feedStatus.state==='live'&&liveBook.ready&&Date.now()-liveBook.time<5000&&liveBook.bids.length&&liveBook.asks.length;
const showError=(message)=>{if($('#orderError')){$('#orderError').textContent=message;$('#orderError').hidden=false;}else toast(message,'error');};

hydrateIcons();
const chart=new TerminalChart($('#chartContainer'),{onCrosshair:c=>updateOHLC(c??candles.at(-1)),onDraw:drawings=>{if(!replayTimer){savedDrawings[currentSymbol]=drawings;save('drawings-v3',savedDrawings);}if(selectedTool!=='crosshair')toast('Desenho adicionado.');},onContextMenu:openChartContext,onExecutionHover:payload=>executionLayer?.hover(payload),onExecutionSelect:payload=>executionLayer?.select(payload),onViewMode:renderChartViewState});
chart.setOptions(chartOptions);
executionLayer=new ExecutionLayer(chart,{openModal,closeModal,notify:toast,openChart:()=>navigation.show('terminal',{pane:'chart'}),openHistory:()=>navigation.show('history')});
const feedCallbacks={
  onStatus:s=>{if(s.state!==feedStatus.state||s.unrecovered!==feedStatus.unrecovered)captureVersion++;if(feedStatus.state==='live'&&s.state!=='live'&&!activeGap)activeGap={from:Date.now(),reason:s.state};if(s.state==='live'&&activeGap){captureGaps.push({...activeGap,to:Date.now()});captureGaps=captureGaps.slice(-100);activeGap=null;captureVersion++;}feedStatus=s;if(s.state!=='live')liveBook.ready=false;renderDirty=true;contractManager?.update();},
  onTicker:t=>{const a=assetMap.get(t.symbol);if(!a||!assets.some(item=>item.symbol===t.symbol))return;for(const key of ['price','bid','ask','open','high','low','volume'])if(Number.isFinite(t[key])||t[key]===null)a[key]=t[key];if(Number.isFinite(t.time))a.priceTime=t.time;a.updatedAt=t.receivedAt??Date.now();a.change=a.open>0&&Number.isFinite(a.price)?(a.price/a.open-1)*100:null;checkAlerts(a);renderDirty=true;},
  onTrade:t=>{if(t.symbol!==currentSymbol||captureIds.has(String(t.id)))return;const a=assetMap.get(t.symbol);if(a&&(a.priceTime==null||t.time>=a.priceTime)){a.price=t.price;a.priceTime=t.time;a.updatedAt=t.receivedAt??Date.now();a.change=a.open>0?(a.price/a.open-1)*100:null;checkAlerts(a);}captureIds.add(String(t.id));captureTrades.push(t);if(captureTrades.length>20000){const removed=captureTrades.shift();captureIds.delete(String(removed.id));}flow.addTrade(t);captureVersion++;if(currentProvider==='rithmic')historyTradeVersion++;addTradeToBuckets(t,liveBuckets);chartDirty=true;renderDirty=true;},
  onBook:b=>{if(b.symbol!==currentSymbol)return;liveBook=b;if(flow.addBook(b))captureVersion++;processPaperOrders();renderDirty=true;},
  onGap:g=>{if(currentProvider==='rithmic'){const time=Number.isFinite(g.time)?g.time:Date.now();captureGaps.push({from:time,to:time,reason:'collector-reported'});captureGaps=captureGaps.slice(-100);captureVersion++;}renderDirty=true;}
};
const coinbaseFeed=createFeed(Object.fromEntries(Object.entries(feedCallbacks).map(([key,callback])=>[key,value=>{if(currentProvider==='coinbase')callback(value);}])));
const qhosBridge=createQhosBridge({
  ...Object.fromEntries(Object.entries(feedCallbacks).map(([key,callback])=>[key,value=>{if(currentProvider==='rithmic')callback(value);} ])),
  onSelection:selection=>adoptQhosSelection(selection),
  onStatus:status=>{if(currentProvider==='rithmic')feedCallbacks.onStatus(status);collectorAuto?.onStatus();contractManager?.update();}
});
const feed={
  start:symbol=>{if(currentProvider==='coinbase')coinbaseFeed.start(symbol,{symbols:coinbaseIds});},
  stop:()=>{coinbaseFeed.stop();qhosBridge.disconnect();},
  reconnect:()=>{if(currentProvider==='coinbase')coinbaseFeed.reconnect();else if(qhosBridge.getSnapshot().sessionId)qhosBridge.reconnect().catch(error=>toast(error.message,'error'));else contractManager.openConnection();}
};
contractManager=new ContractManagerUI({
  openModal,closeModal,getState:contractState,notify:toast,
  search:async(query,{provider,signal}={})=>provider==='rithmic'?qhosBridge.search(query,{signal}):coinbaseAssets.filter(a=>`${a.displaySymbol} ${a.name}`.toLocaleLowerCase('pt-BR').includes(query.trim().toLocaleLowerCase('pt-BR'))).map(instrumentMetadata),
  applySelection:applyContractSelection,
  connect:async config=>{
    const automatic=autoConnectDispatch;
    if(!automatic)collectorAuto?.manualConnect(config.url);
    try{const pending=qhosBridge.connect(config);config.token='';const welcome=await pending;qhosEndpoint=config.url;persistSource();contractManager?.update();return welcome;}
    finally{config.token='';if(!automatic)collectorAuto?.manualConnectDone();}
  },
  disconnect:()=>{collectorAuto.manualDisconnect();liveBook.ready=false;renderDirty=true;},
  useCoinbase:()=>applyContractSelection({provider:'coinbase',ids:coinbaseIds,activeId:coinbaseIds[0]})
});
const COLLECTOR_AUTO_ORIGIN="http://127.0.0.1:8766";
// Same-origin bootstrap. Credentials are transient input to the bridge, never UI state.
function createCollectorAutoConnection({location,allowedOrigin,source,getBridgeStatus,connect,disconnect,onChange=()=>{},fetchImpl=(...args)=>fetch(...args),now=()=>Date.now(),setTimeout:later=(fn,ms)=>setTimeout(fn,ms),clearTimeout:cancel=id=>clearTimeout(id),document:page=globalThis.document}){
  const RETRY_MS=10000,HIDDEN_MS=300000;
  const ownOrigin=typeof allowedOrigin==='string'&&/^http:\/\/127\.0\.0\.1:[1-9][0-9]*$/.test(allowedOrigin)&&location?.origin===allowedOrigin;
  const expectedSocket=ownOrigin?`ws://${new URL(allowedOrigin).host}`:null;
  let enabled=ownOrigin&&(source==null||source==='rithmic'),started=false,destroyed=false,manualDisconnected=false,manualPending=false,manualOnly=false;
  let attempted=false,paused=false,message='',generation=0,flight=null,retryTimer=null,hiddenTimer=null,hiddenSince=page?.hidden?now():null,lastNotification='';
  function bridge(){return getBridgeStatus()??{};}
  function snapshot(){const s=bridge();return {active:started&&enabled&&!manualDisconnected&&!manualPending&&!paused&&!destroyed&&!s.connected,attempting:!!flight&&flight.generation===generation&&!s.connected,message,manualDisconnected,attempted,paused,manualOnly};}
  function changed(){const state=snapshot(),key=JSON.stringify(state);if(key!==lastNotification){lastNotification=key;onChange(state);}}
  function clearRetry(){cancel(retryTimer);retryTimer=null;}
  function invalidate(){generation++;clearRetry();flight?.abort.abort();}
  function eligible(){return started&&enabled&&!manualDisconnected&&!manualPending&&!paused&&!destroyed;}
  function busy(){const s=bridge();return s.connected||s.retrying||s.state==='connecting';}
  function schedule(){if(!eligible()||busy()||flight||retryTimer!==null)return;retryTimer=later(()=>{retryTimer=null;attempt();},RETRY_MS);}
  function rejectConfiguration(){
    enabled=false;manualOnly=true;attempted=false;clearRetry();
    message='Conexão automática indisponível. Configure a conexão manual.';
  }
  async function attempt(){
    if(!eligible()||busy()||flight)return;
    clearRetry();attempted=true;message='Conectando ao coletor';
    const run={generation,abort:new AbortController()};flight=run;changed();
    let payload=null,config=null,fetchTimer=later(()=>run.abort.abort(),RETRY_MS);
    try{
      const response=await fetchImpl('/conexao',{cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer',redirect:'error',signal:run.abort.signal});
      if(run.generation!==generation||!eligible())return;
      if(!response.ok){message=response.status===404?'Conexão automática indisponível. Nova tentativa em 10 s.':'Coletor indisponível. Nova tentativa em 10 s.';return;}
      if(response.status!==200){rejectConfiguration();return;}
      if(response.url){let sameOrigin=false;try{sameOrigin=new URL(response.url).origin===allowedOrigin;}catch{}if(!sameOrigin){rejectConfiguration();return;}}
      try{payload=await response.json();}catch{if(run.generation===generation&&eligible())rejectConfiguration();return;}
      if(run.generation!==generation||!eligible())return;
      if(!payload||typeof payload!=='object'||Array.isArray(payload)||typeof payload.token!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(payload.token)||payload.url!==expectedSocket){
        rejectConfiguration();return;
      }
      // Discovery requires the literal endpoint; URL normalization is reserved for the manual flow.
      // A manual action or bridge retry can win while the response body is read.
      if(busy())return;
      cancel(fetchTimer);fetchTimer=null;
      config={url:payload.url,token:payload.token};
      const pending=connect(config);
      config.token='';payload.token='';payload=null;
      await pending;
      if(run.generation===generation)message='';
    }catch{
      // Never reflect fetch/JSON/transport errors: any of them may echo a credential.
      if(run.generation===generation)message='Coletor indisponível. Nova tentativa em 10 s.';
    }finally{
      cancel(fetchTimer);
      if(config)config.token='';
      if(payload&&typeof payload==='object'&&!Object.isFrozen(payload))payload.token='';
      if(flight===run)flight=null;
      if(!destroyed){schedule();changed();}
    }
  }
  function onStatus(){
    const s=bridge();
    if(s.connected){clearRetry();message='';}
    else if(s.retrying||s.state==='connecting')clearRetry();
    else schedule();
    changed();
  }
  function visibilityChanged(){
    cancel(hiddenTimer);hiddenTimer=null;
    if(page?.hidden){
      if(hiddenSince===null)hiddenSince=now();
      const remaining=HIDDEN_MS-(now()-hiddenSince);
      if(remaining>0){hiddenTimer=later(visibilityChanged,remaining);return;}
      if(!paused){paused=true;invalidate();message='Conexão automática pausada enquanto esta aba está oculta.';changed();}
      return;
    }
    hiddenSince=null;
    const resume=paused;paused=false;
    if(resume){message='';changed();attempt();}
  }
  function start(){if(started||destroyed)return;started=true;page?.addEventListener?.('visibilitychange',visibilityChanged);visibilityChanged();attempt();}
  function setSource(provider){
    if(provider!=='coinbase')return;
    enabled=false;manualOnly=false;manualPending=false;manualDisconnected=true;message='';invalidate();disconnect();changed();
  }
  function manualDisconnect(){enabled=false;manualPending=false;manualDisconnected=true;message='Desconectado por você.';invalidate();disconnect();changed();}
  function manualConnect(url){
    invalidate();manualOnly=false;manualDisconnected=false;manualPending=true;attempted=true;message='';
    try{enabled=ownOrigin&&new URL(url).href===new URL(expectedSocket).href;}catch{enabled=false;}
    changed();
  }
  function manualConnectDone(){manualPending=false;onStatus();}
  function destroy(){destroyed=true;invalidate();cancel(hiddenTimer);page?.removeEventListener?.('visibilitychange',visibilityChanged);}
  return Object.freeze({start,onStatus,setSource,manualDisconnect,manualConnect,manualConnectDone,visibilityChanged,getSnapshot:snapshot,destroy});
}

// Link health and market-data health are intentionally displayed separately.
// Colors and labels match renderRithmicPath without changing that screen.
const COLLECTOR_LAYER_NAMES={rithmic:'Rithmic',sessao:'Sessão',gravacao:'Gravação',livro:'Livro',leitura:'Leitura',coletor:'Coletor'};
const COLLECTOR_LIGHT_STATES={ok:{label:'OK',color:'#79c27a'},atencao:{label:'Atenção',color:'#e6b969'},falha:{label:'Falha',color:'#e2675f'},desconhecido:{label:'Desconhecido',color:'#607080'}};
function collectorLinkLight(status={},auto={}){
  const reconnects=Number.isSafeInteger(status.reconnects)&&status.reconnects>=0?status.reconnects:0;
  let state='desconhecido',reason='Nunca conectou';
  if(auto.manualDisconnected){reason='Desconectado pelo usuário';}
  else if(status.state==='error'){state='falha';reason='Erro na conexão com o coletor';}
  else if(status.connected&&status.subscriptionConfirmed&&['waiting','live','stale'].includes(status.state)){state='ok';reason='Conexão e assinatura confirmadas';}
  else if(status.state==='connecting'||status.retrying||auto.attempting||auto.active){state='atencao';reason=(status.retrying||reconnects>0)?'Reconectando ao coletor':'Conectando ao coletor';}
  else if(auto.attempted||status.startedAt!==null&&status.startedAt!==undefined||status.lastMessageAt!==null&&status.lastMessageAt!==undefined){state='falha';reason=auto.paused?'Reconexão pausada com a aba oculta':'Desconectado sem nova tentativa agendada';}
  return {id:'terminal',name:'Terminal↔Coletor',state,reason,since:null,reconnects};
}
function collectorLayerLights(status={},now=Date.now()){
  const stages=Array.isArray(status.pipeline?.stages)?status.pipeline.stages:[];
  const receivedAt=status.pipeline?.receivedAt,age=now-receivedAt;
  const fresh=status.connected&&status.capabilities?.includes('pipeline')&&typeof receivedAt==='number'&&Number.isFinite(receivedAt)&&age>=0&&age<=5000;
  const ordered=[],seen=new Set();
  for(const stage of stages){if(stage&&Object.hasOwn(COLLECTOR_LAYER_NAMES,stage.id)&&!seen.has(stage.id)){ordered.push(stage);seen.add(stage.id);}}
  for(const id of Object.keys(COLLECTOR_LAYER_NAMES)){if(!seen.has(id))ordered.push({id});}
  return ordered.map(stage=>{
    const known=Object.hasOwn(COLLECTOR_LIGHT_STATES,stage.state);
    const state=fresh&&known?stage.state:'desconhecido';
    const reason=!status.connected?'Coletor desconectado':!status.capabilities?.includes('pipeline')?'Coletor não informa o percurso':!status.pipeline?'Percurso não recebido':!fresh?`Percurso sem atualização${Number.isFinite(age)?` há ${Math.max(0,Math.floor(age/1000))} s`:''}`:typeof stage.reason==='string'?stage.reason:'Sem informação';
    const reportedReason=typeof stage.reason==='string'?stage.reason:'';
    return {id:stage.id,name:COLLECTOR_LAYER_NAMES[stage.id],state,reason:fresh?reason:reportedReason?`${reason} · último motivo recebido: ${reportedReason}`:reason,since:Number.isFinite(stage.since)&&stage.since>=0&&stage.since<=8640000000000000?stage.since:null};
  });
}
function collectorLightPresentation(light){
  const {label,color}=COLLECTOR_LIGHT_STATES[light.state]??COLLECTOR_LIGHT_STATES.desconhecido;
  const since=displayTime.dateTime(light.since,{milliseconds:true});
  const detail=`${light.reason} · desde ${since}${light.id==='terminal'?` · ${light.reconnects} reconexões`:''}`;
  const visible=light.id==='terminal'?`${light.name} · ${label} · ${light.reconnects} reconexões`:`${light.name} · ${label}`;
  return {color,detail,visible};
}
function collectorLightHTML(light){
  const {color,detail,visible}=collectorLightPresentation(light);
  return `<span class="collector-light" tabindex="0" data-collector-layer="${esc(light.id)}" data-state="${esc(light.state)}" style="--collector-light-color:${color}" title="${esc(detail)}" aria-label="${esc(visible+' · '+detail)}"><i aria-hidden="true"></i><span>${esc(visible)}</span><span class="collector-light-tooltip" role="tooltip">${esc(detail)}</span></span>`;
}
function renderCollectorLights(){
  const strip=$('#collectorLights');if(!strip)return;
  const status=qhosBridge.getSnapshot(),auto=collectorAuto.getSnapshot();
  const lights=[collectorLinkLight(status,auto),...collectorLayerLights(status)];
  lights.forEach((light,index)=>{
    const {color,detail,visible}=collectorLightPresentation(light);
    let node=strip.querySelector(`[data-collector-layer="${light.id}"]`);
    if(!node){const holder=document.createElement('div');holder.innerHTML=collectorLightHTML(light);node=holder.firstElementChild;}
    if(node.dataset.state!==light.state)node.dataset.state=light.state;
    node.style.setProperty('--collector-light-color',color);
    if(node.title!==detail)node.title=detail;
    if(node.getAttribute('aria-label')!==visible+' · '+detail)node.setAttribute('aria-label',visible+' · '+detail);
    if(node.children[1].textContent!==visible)node.children[1].textContent=visible;
    if(node.children[2].textContent!==detail)node.children[2].textContent=detail;
    if(strip.children[index]!==node)strip.insertBefore(node,strip.children[index]??null);
  });
}

collectorAuto=createCollectorAutoConnection({
  location,allowedOrigin:COLLECTOR_AUTO_ORIGIN,source:savedSource.provider,
  getBridgeStatus:()=>qhosBridge.getSnapshot(),disconnect:()=>qhosBridge.disconnect(),
  connect:config=>{autoConnectDispatch=true;try{return contractManager.connect(config);}finally{autoConnectDispatch=false;}},
  onChange:()=>{renderDirty=true;contractManager?.update();renderStatus();}
});
const microstructurePanel=new MicrostructurePanel($('#microstructurePanel'),{
  notify:toast,openSessions,listSessions,getSession,
  openChart:()=>navigation.show('terminal',{pane:'chart'}),
  openAnalysis:openAnalysisScreen,
  focusExecution:id=>executionLayer.focus(id)
});
const navigation=new ScreenNavigation({
  onTerminal:()=>{fitWorkspace();chart.resize();renderAnalysis();$('#floatingMenu').hidden=true;},
  onResearch:screen=>{microstructurePanel.showView(screen);updateMicrostructure();$('#floatingMenu').hidden=true;executionLayer.hover(null);},
  onHistory:()=>{$('#floatingMenu').hidden=true;executionLayer.hover(null);},
  onPane:pane=>{if(pane&&pane!=='chart'&&workspace.mode==='focus'){workspace.mode='balanced';applyWorkspace();}fitWorkspace();chart.resize();renderAnalysis();}
});
function openAnalysisScreen(tab){navigation.show('terminal',{pane:'analysis',focus:false});selectAnalysis(tab);$(`[data-analysis="${tab}"]`)?.focus({preventScroll:true});}
function instrumentMetadata(asset){
  const {id,provider,name,exchange,currency,assetClass,expiry,tickSize,priceDecimals,quantityUnit,contractMultiplier,capabilities}=asset;
  return {id,symbol:asset.displaySymbol??asset.symbol,provider,name,exchange,currency,assetClass,expiry,tickSize,priceDecimals,quantityUnit,contractMultiplier,capabilities};
}
function sourceMetadata(asset){return {instrument:instrumentMetadata(asset),sourceLabel:asset.sourceLabel,environment:asset.provider==='rithmic'?asset.environment??null:'production'};}
function viewMetadata(){return replayTimer?{instrument:replayData.instrument??instrumentMetadata(replayAsset),sourceLabel:sourceLabel(),environment:replayData.environment??null}:sourceMetadata(currentAsset());}
function registerInstrument(instrument,options){
  const environment=options?.environment??qhosEnvironment;
  const asset={...instrument,symbol:instrument.id,displaySymbol:instrument.symbol,environment,sourceLabel:`Rithmic · ${environment==='test'?'Test':environment==='paper'?'Paper':environment==='synthetic'?'sintético':environment==='production'?'produção':'origem desconhecida'}`,icon:instrument.symbol.slice(0,2),color:'#2f9d89',price:null,change:null,bid:null,ask:null,open:null,high:null,low:null,volume:null,updatedAt:0};
  if(options?.register!==false)assetMap.set(asset.symbol,asset);return asset;
}
function persistSource(){save('source-v4',{provider:currentProvider,endpoint:qhosEndpoint,environment:qhosEnvironment,coinbaseIds,activeId:currentSymbol,instruments:currentProvider==='rithmic'?assets.map(instrumentMetadata):[]});}
function contractState(){
  const connection=qhosBridge.getSnapshot();
  return {provider:currentProvider,connection:{...connection,endpoint:qhosEndpoint,autoConnect:collectorAuto?.getSnapshot()},instruments:[...coinbaseAssets,...assets.filter(a=>a.provider==='rithmic')].map(instrumentMetadata),activeId:currentSymbol,watchlistIds:assets.map(a=>a.id),maxInstruments:3};
}
function adoptQhosSelection(selection){
  if(replayTimer)stopReplay(false);
  if(workspace.autoSave)checkpointSession();
  qhosEnvironment=qhosBridge.getSnapshot().environment;
  currentProvider='rithmic';coinbaseFeed.stop();
  assets.splice(0,assets.length,...selection.instruments.map(registerInstrument));
  activateInstrument(selection.activeId,{checkpoint:false});
  persistSource();contractManager?.update();
}
async function applyContractSelection({provider,ids,activeId}){
  if(!Array.isArray(ids)||ids.length<1||ids.length>3||new Set(ids).size!==ids.length||!ids.includes(activeId))throw new Error('Selecione de um a três contratos e indique o ativo do gráfico.');
  if(provider==='rithmic')return qhosBridge.setSelection({ids,activeId});
  if(provider!=='coinbase'||ids.some(id=>!coinbaseAssets.some(a=>a.id===id)))throw new Error('Ativo indisponível nesta fonte.');
  if(replayTimer)stopReplay(false);
  if(workspace.autoSave)checkpointSession();
  collectorAuto.setSource('coinbase');currentProvider='coinbase';qhosBridge.disconnect();coinbaseIds=[...ids];
  assets.splice(0,assets.length,...coinbaseIds.map(id=>assetMap.get(id)));
  activateInstrument(activeId,{checkpoint:false});persistSource();contractManager?.update();
  return {activeId,instruments:assets.map(instrumentMetadata)};
}
function fitWorkspace(){
  const center=$('.center-workspace');
  if($('#terminalScreen').hidden||center.clientHeight<1)return;
  const style=getComputedStyle(center),available=center.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom);
  const chartReserve=Math.min(320,Math.max(180,available*.55));
  const maximum=Math.max(100,Math.min(520,Math.floor(available-chartReserve-10)));
  const minimum=Math.min(140,maximum),actual=Math.round(Math.max(minimum,Math.min(workspace.panelHeight,maximum)));
  document.documentElement.style.setProperty('--analysis-effective-height',`${actual}px`);
  $('#panelResizer').setAttribute('aria-valuemin',String(minimum));
  $('#panelResizer').setAttribute('aria-valuemax',String(maximum));
  $('#panelResizer').setAttribute('aria-valuenow',String(actual));
  $('#panelResizer').setAttribute('aria-valuetext',`${actual} pixels`);
}
const workspaceObserver=new ResizeObserver(()=>fitWorkspace());
workspaceObserver.observe($('.center-workspace'));

function updateMicrostructure(){
  const data=analysisData(),replaying=!!replayTimer,until=replaying?replayTime:Date.now();
  const frame=replaying?(replayData?.timeframe??currentTimeframe):currentTimeframe;
  microstructurePanel.update({
    symbol:viewSymbol(),...viewMetadata(),trades:data.trades??[],books:data.books??[],candles,until,
    intervalSeconds:frameSeconds[frame],replaying,replayPaused,imported:replaying&&!!replayData?.imported,
    feedStatus:replaying?(replayData?.diagnostics??{state:'replay'}):feedStatus,
    pipeline:!replaying&&currentProvider==='rithmic'?qhosBridge.getSnapshot().pipeline:null,
    liveBook:replaying?replayFlow?.getAt(until).book:liveBook,
    assets:replaying?[]:assets.map(a=>({symbol:a.symbol,displaySymbol:a.displaySymbol,currency:a.currency,priceDecimals:a.priceDecimals,provider:a.provider,price:a.price,bid:a.bid,ask:a.ask,updatedAt:a.updatedAt})),
    captureCreatedAt:replaying?replayData?.createdAt:captureCreatedAt,
    lastSavedAt,sessionCount:sessionCatalog.length,storageError,autoSave:workspace.autoSave,
    recording:data.recording??flow.getRecording(),
    gaps:replaying?(replayData?.gaps??[]):[...captureGaps,...(activeGap?[{...activeGap,to:until}]:[])],
    diagnosticSummary:replaying?(replayData?.diagnostics??{}):feedStatus
  });
}

function toast(message,type='success'){const el=document.createElement('div');el.className=`toast ${type==='error'?'error':''}`;el.innerHTML=`${icon(type==='error'?'info':'check-circle')}<span>${esc(message)}</span>`;$('#toastStack').appendChild(el);setTimeout(()=>el.remove(),4500);}
function addTradeToBuckets(t,map,frame=currentTimeframe){const seconds=frameSeconds[frame],key=Math.floor(t.time/1000/seconds)*seconds;let b=map.get(key);if(!b){b={time:key,open:t.price,high:t.price,low:t.price,close:t.price,volume:0,first:t.time,last:t.time,captured:true};map.set(key,b);if(map.size>500)map.delete(Math.min(...map.keys()));}b.high=Math.max(b.high,t.price);b.low=Math.min(b.low,t.price);b.volume+=t.size;if(t.time<b.first){b.first=t.time;b.open=t.price;}if(t.time>=b.last){b.last=t.time;b.close=t.price;}}
function rebuildBuckets(){liveBuckets=new Map();for(const t of captureTrades)addTradeToBuckets(t,liveBuckets);}
function composeCandles(until=null,source=captureTrades){const frame=until===null?currentTimeframe:(replayData?.timeframe??currentTimeframe),history=until===null?historyCandles:(replayData?.historyCandles??[]),map=until===null?liveBuckets:new Map();if(until!==null)for(const t of source)if(t.time<=until)addTradeToBuckets(t,map,frame);const boundary=until===null?historyBoundary:Math.floor((source[0]?.time??until)/1000/frameSeconds[frame])*frameSeconds[frame];const base=history.filter(c=>c.time<boundary&&(until===null||(c.time+frameSeconds[frame])*1000<=until));return [...base,...[...map.values()].filter(c=>c.time>=boundary).sort((a,b)=>a.time-b.time)].slice(-500);}
function paintChart(){candles=composeCandles();chart.setData(candles,currentAsset());if(candles.length)$('#chartMessage').hidden=true;if(chartDrawingSymbol!==currentSymbol&&candles.length){chart.setDrawings?.(savedDrawings[currentSymbol]??[]);chartDrawingSymbol=currentSymbol;}updateOHLC(candles.at(-1));updateLegend();updateRiskMarkers();refreshExecutions();}
function refreshExecutions(){
  if(!executionLayer)return;
  const frame=replayTimer?(replayData?.timeframe??currentTimeframe):currentTimeframe;
  const key=[viewSymbol(),frame,replayTimer?'replay':'live',replayTimer?replayTime:captureVersion,paperExecutionVersion,replayData?.id??''].join('|');
  if(key===executionContextKey)return;executionContextKey=key;
  const data=analysisData();
  executionLayer.update({...viewMetadata(),trades:data.trades??[],paperHistory:replayTimer?[]:account.history,symbol:viewSymbol(),until:replayTimer?replayTime:Infinity,intervalSeconds:frameSeconds[frame],imported:!!replayData?.imported,replaying:!!replayTimer});
}
function refreshRithmicHistory(){
  if(document.hidden||replayTimer||currentProvider!=='rithmic'||historyPendingVersion||historyTradeVersion<=rithmicHistoryCoveredVersion||Date.now()-rithmicHistoryAttemptAt<2000)return;
  const auto=collectorAuto?.getSnapshot(),status=qhosBridge.getSnapshot(),asset=assetMap.get(currentSymbol);
  if(auto?.manualOnly||auto?.active||!status.connected||!status.subscriptionConfirmed||!status.capabilities.includes('history')||!asset?.capabilities.history)return;
  loadChart(true);
}
async function loadChart(quiet=false){
  if(replayTimer||collectorAuto?.getSnapshot().manualOnly)return;
  if(quiet&&currentProvider==='rithmic'&&(historyPendingVersion||Date.now()-rithmicHistoryAttemptAt<2000))return;
  const version=++historyVersion;historyAbort?.abort();historyAbort=new AbortController();historyPendingVersion=0;
  const a=assetMap.get(currentSymbol),symbol=currentSymbol,frame=currentTimeframe,provider=currentProvider;
  renderChartIdentity();$('#chartTimeframe').textContent=frame;
  $$('.timeframes button').forEach(b=>{b.classList.toggle('active',b.dataset.timeframe===frame);b.setAttribute('aria-pressed',String(b.dataset.timeframe===frame));});
  if(!quiet){executionContextKey='';executionLayer?.clearSelection();executionLayer?.update({...sourceMetadata(a),trades:[],paperHistory:[],symbol,until:Date.now(),intervalSeconds:frameSeconds[frame],replaying:false});historyCandles=[];historyUpdatedAt=0;historyError='';historyCoverage=null;candles=[];chart.setData([],a);updateOHLC(candles.at(-1));updateLegend();$('#chartMessage').hidden=false;$('#chartMessage').textContent=`Aguardando dados de ${a.displaySymbol}…`;}
  if(!quiet)rebuildBuckets();
  if(provider==='rithmic'&&!qhosBridge.getSnapshot().connected){historyError='Conecte o coletor QHOS para receber este contrato.';renderDirty=true;return;}
  if(provider==='rithmic'&&!a.capabilities.history){historyBoundary=Math.floor(captureCreatedAt/1000/frameSeconds[frame])*frameSeconds[frame];historyError='Histórico indisponível para este contrato; candles formados a partir dos negócios classificados recebidos.';renderDirty=true;return;}
  // A snapshot has no trade watermark. Keep its buckets authoritative instead
  // of adding potentially overlapping live volume. Arrivals during the request
  // remain dirty and cause a later, bounded refresh.
  const requestTradeVersion=historyTradeVersion;
  historyPendingVersion=version;if(provider==='rithmic')rithmicHistoryAttemptAt=Date.now();
  try{
    const data=await(provider==='rithmic'?qhosBridge.loadCandles(symbol,frame,{signal:historyAbort.signal}):loadCandles(symbol,frame,{signal:historyAbort.signal}));
    if(version!==historyVersion)return;
    historyCoverage=provider==='rithmic'?(data.coverage??null):null;
    if(provider==='rithmic'){
      historyCandles=data;
      historyBoundary=data.length?data.at(-1).time+frameSeconds[frame]:Math.floor(captureCreatedAt/1000/frameSeconds[frame])*frameSeconds[frame];
      rithmicHistoryCoveredVersion=requestTradeVersion;
    }else{
      historyBoundary=Math.floor(Date.now()/1000/frameSeconds[frame])*frameSeconds[frame];historyCandles=data.filter(c=>c.time<historyBoundary);
    }
    historyUpdatedAt=Date.now();captureVersion++;historyError='';$('#chartMessage').hidden=true;if(replayTimer)paintReplay();else paintChart();
  }catch(e){if(e.name==='AbortError'||version!==historyVersion)return;historyError=e.message||'Falha ao carregar histórico';if(!historyCandles.length&&!replayTimer){$('#chartMessage').hidden=false;$('#chartMessage').innerHTML='Histórico indisponível.<br><button id="retryHistory">Tentar novamente</button>';$('#retryHistory').onclick=()=>loadChart();}if(!quiet)toast('Não foi possível carregar os candles. O status do feed mostra o diagnóstico.','error');}
  finally{if(historyPendingVersion===version)historyPendingVersion=0;}
  renderDirty=true;
}
function renderChartIdentity(){const a=currentAsset();$('#toolbarSymbol').textContent=a.displaySymbol;$('#symbolSelect .asset-icon').textContent=a.icon;$('#symbolSelect .asset-icon').style.background=a.color;$('#instrumentName').textContent=a.provider==='coinbase'?`${a.name} / ${a.currency}`:`${a.name} · ${a.displaySymbol}`;$('#exchangeTag').textContent=a.exchange;$('#chartSource').title=sourceLabel();}
function activateInstrument(symbol,{checkpoint=true}={}){
  if(!assetMap.has(symbol))return;
  if(replayTimer)stopReplay(false);
  if(checkpoint&&workspace.autoSave)checkpointSession();
  if(activeModal==='order'||activeModal==='protection')closeModal();
  currentSymbol=symbol;chartDrawingSymbol='';captureTrades=[];captureIds.clear();liveBuckets.clear();liveBook={bids:[],asks:[],ready:false,time:0};flow.reset(symbol,sourceMetadata(assetMap.get(symbol)));
  rithmicHistoryCoveredVersion=historyTradeVersion;rithmicHistoryAttemptAt=0;
  captureSessionId=uid();captureCreatedAt=Date.now();captureName=sessionName();captureVersion++;lastSavedVersion=-1;lastSavedAt=0;captureGaps=[];activeGap=null;footprintOffset=0;
  historyBoundary=Math.floor(captureCreatedAt/1000/frameSeconds[currentTimeframe])*frameSeconds[currentTimeframe];
  feedStatus={state:currentProvider==='rithmic'?'waiting':'connecting',gaps:0,reconnects:0};
  setTool('crosshair',false);loadChart();feed.start(symbol);renderAnalysisToolbar();renderTicker();renderWatchlist();renderDetails();renderFlow();renderDirty=true;persistPrefs();
}
async function setSymbol(symbol){
  if(replayTimer)stopReplay(false);
  if(symbol===currentSymbol)return;
  if(!assets.some(a=>a.symbol===symbol)){toast('Abra a busca para adicionar este ativo à fonte correspondente.','error');return;}
  try{if(currentProvider==='rithmic'){await qhosBridge.setSelection({ids:assets.map(a=>a.id),activeId:symbol});}else{activateInstrument(symbol);persistSource();}}catch(error){toast(error.message,'error');}
}
function updateOHLC(c){if(!c){$('#ohlc').innerHTML='';return;}const a=currentAsset();$('#ohlc').classList.toggle('down',c.close<c.open);$('#ohlc').innerHTML=[['A',c.open],['M',c.high],['B',c.low],['F',c.close]].map(([k,v])=>`<span>${k}<b>${formatPrice(v,a)}</b></span>`).join('');}
function updateLegend(){$('#indicatorLegend').innerHTML=[['ema20','EMA 20','#dba75c',20],['ema50','EMA 50','#6e92dc',50]].filter(([k])=>chartOptions[k]).map(([k,label,color,p])=>`<div class="legend-item"><span style="background:${color}"></span>${label}<b style="color:${color}">${formatPrice(ema(candles,p).at(-1),currentAsset())}</b></div>`).join('');}
function renderTicker(){
  $('#tickerStrip').innerHTML=assets.map(a=>`<button class="ticker-item ${currentSymbol===a.symbol?'selected':''}" data-symbol="${esc(a.symbol)}">${badge(a)}<div class="ticker-info"><span class="ticker-name">${esc(a.name.toUpperCase())} <small>${esc(a.displaySymbol)}</small></span><span class="ticker-values">${formatPrice(a.price,a)}<span class="ticker-change ${a.change>=0?'positive':'negative'}">${percent(a.change)}</span></span></div><span class="ticker-source">${esc(a.exchange)}<br><b>${a.updatedAt?ageText(a.updatedAt):'aguardando'}</b></span></button>`).join('')+`<div class="ticker-context"><div>${currentProvider==='rithmic'?'RITHMIC · '+(qhosEnvironment==='test'?'TEST':'QHOS'):'COINBASE'} <small>${currentProvider==='rithmic'?'Contratos via coletor QHOS':'Mercado spot · saldo virtual'}</small></div></div>`;
}
function renderWatchlist(){
  $('#watchlistSource').textContent=currentProvider==='rithmic'?'RITHMIC · '+(qhosEnvironment==='test'?'TEST':'QHOS'):'COINBASE · USD';
  $('#sortWatchlist').innerHTML=`${currentProvider==='coinbase'?'24h':'Variação'} % ${icon('sort')}`;
  let list=[...assets];if(sortDirection)list.sort((a,b)=>((b.change??0)-(a.change??0))*sortDirection);
  $('#watchlist').innerHTML=list.map(a=>`<button class="watchlist-row ${a.symbol===currentSymbol?'active':''}" data-symbol="${esc(a.symbol)}" aria-pressed="${a.symbol===currentSymbol}" title="${esc(a.name+' · '+a.exchange+(a.expiry?' · vence '+expiryText(a.expiry):''))}"><span class="watchlist-asset">${badge(a)}${esc(a.displaySymbol)}</span><span class="watchlist-price">${formatPrice(a.price,a)}</span><span class="watchlist-change ${a.change>=0?'positive':'negative'}">${percent(a.change)}</span></button>`).join('');$('#favoriteButton').hidden=true;
}
function renderDetails(){
  const a=currentAsset(),view=replayTimer?replayFlow?.getAt(replayTime):null,price=replayTimer?(view?.price??null):a.price,b=replayTimer?(view?.book??{bids:[],asks:[]}):liveBook;
  $('#detailIcon').textContent=a.icon;$('#detailIcon').style.background=a.color;$('#detailSymbol').textContent=a.displaySymbol;
  $('#detailName').textContent=replayTimer?'Replay da captura':`${a.sourceLabel}${a.expiry?' · '+expiryText(a.expiry):''}`;
  $('#detailPrice').textContent=formatPrice(price,a);$('#detailCurrency').textContent=a.currency;
  $('#detailChange').className=`price-change ${a.change>=0?'positive':'negative'}`;
  $('#detailChange').innerHTML=replayTimer?`<span>${timeMarkup(replayTime,'zoned')} · captura local</span>`:`${percent(a.change)} <span>${a.provider==='coinbase'?'24h · Coinbase':'Referência informada pelo coletor'}</span>`;
  $('#sellPrice').textContent=formatPrice(b.bids?.[0]?.[0]??(replayTimer?null:a.bid),a);$('#buyPrice').textContent=formatPrice(b.asks?.[0]?.[0]??(replayTimer?null:a.ask),a);$('#flowUnit').textContent=unit();
  for(const id of ['buyButton','sellButton','newOrderButton']){$('#'+id).disabled=!paperSupported();$('#'+id).title=paperSupported()?'Simular com saldo virtual':'Simulador disponível para os pares spot Coinbase';}
}

function selectAnalysis(tab){analysisTab=tab;$$('[data-analysis]').forEach(b=>{b.classList.toggle('active',b.dataset.analysis===tab);b.setAttribute('aria-selected',String(b.dataset.analysis===tab));b.tabIndex=b.dataset.analysis===tab?0:-1;});for(const key of ['footprint','cvd','heatmap','profile','paper','health'])$(`#${key}Surface`).hidden=tab!==key;renderAnalysisToolbar();renderDirty=true;renderAnalysis();}
function displayFlow(){return replayTimer?replayFlow:flow;}
function displaySummary(){if(!replayTimer)return flow.getSummary();return {...replayFlow.getAt(replayTime).summary,truncated:!!replayData.recording?.tradeTruncated};}
function flowStep(){const s=Number(flowPrefs.steps[viewSymbol()]);return s>0&&Number.isFinite(s)?s:({BTCUSD:10,ETHUSD:1,SOLUSD:0.1})[viewSymbol()]??currentAsset()?.tickSize??1;}
function scopeNote(s){return `${replayTimer?'REPLAY · ':''}${s.truncated?'Janela retida':'Captura'} ${rangeMarkup(s.from,replayTimer?replayTime:s.to)} · ${compact(s.count)} negócios`;}
function renderFlow(){
  const s=displaySummary(),book=replayTimer?replayFlow.getAt(replayTime).book:liveBook,b=book??{bids:[],asks:[]};
  $('#flowSummary').innerHTML=`<span>Δ captura <b class="${s.delta>=0?'positive':'negative'}">${s.delta>=0?'+':''}${quantity(s.delta)}</b></span><span>VWAP <b>${formatPrice(s.vwap,currentAsset())}</b></span>`;
  if(currentAsset()?.capabilities?.[flowTab==='dom'?'depth':'trades']===false){$('#flowContent').innerHTML='<div class="flow-empty"><strong>Dados indisponíveis para este contrato</strong><span>A conexão não disponibilizou este tipo de dado.</span></div>';$('#flowCaption').textContent=sourceLabel();return;}
  if(flowTab==='dom'){
    if(!b.ready||!b.bids?.length||!b.asks?.length){$('#flowContent').innerHTML=`<div class="flow-empty">${icon('layers')}<strong>${replayTimer?'Sem snapshot neste instante':'Aguardando snapshot do livro'}</strong><span>${replayTimer?'Avance a reprodução para o primeiro snapshot.':'A profundidade aparece após a sincronização.'}</span></div>`;}
    else{const bids=b.bids.slice(0,9),asks=b.asks.slice(0,9).reverse(),max=Math.max(...bids.map(x=>x[1]),...asks.map(x=>x[1]),1e-9),spread=b.asks[0][0]-b.bids[0][0];
      const row=([p,q],side)=>`<div class="dom-row ${side}" ${paperSupported()?`role="button" tabindex="0" data-order-price="${p}" data-order-side="${side==='bid'?'buy':'sell'}" title="Abrir rascunho de ${side==='bid'?'compra':'venda'} limitada neste preço"`: `aria-label="${side==='bid'?'Bid':'Ask'} ${formatPrice(p,currentAsset())} · ${quantity(q)} ${esc(unit())}"`}><span class="depth-fill" style="width:${q/max*100}%"></span><span class="dom-bid">${side==='bid'?quantity(q):''}</span><strong>${formatPrice(p,currentAsset())}</strong><span class="dom-ask">${side==='ask'?quantity(q):''}</span></div>`;
      $('#flowContent').innerHTML=`<div class="dom-columns"><span>Bid / ${esc(unit())}</span><span>Preço / ${currency()}</span><span>Ask / ${esc(unit())}</span></div><div class="dom-levels">${asks.map(x=>row(x,'ask')).join('')}<div class="dom-spread"><span>SPREAD</span><b>${formatPrice(spread,currentAsset())} ${currency()}</b><span>${num(spread/b.asks[0][0]*10000,2)} bps</span></div>${bids.map(x=>row(x,'bid')).join('')}</div>`;
    }
    $('#flowCaption').textContent=replayTimer?`Último snapshot: ${b.time?num(Math.max(0,replayTime-b.time)/1000,1)+' s antes do cursor':'—'} · até 1/s`:`L2 por preço · ${b.time?ageText(b.time):'—'} desde atualização`;
  }else{
    const trades=replayTimer?replayFlow.getAt(replayTime).trades:flow.getTrades(70);
    $('#flowContent').innerHTML=trades.length?`<div class="tape-columns"><span data-display-zone>${displayTime.label()}</span><span>Preço</span><span>${esc(unit())}</span><span>Agressor</span></div><div class="tape-rows">${trades.map(t=>`<div class="tape-row ${t.side}"><time>${timeMarkup(t.time)}</time><strong>${formatPrice(t.price,currentAsset())}</strong><span>${quantity(t.size)}</span><b>${t.side==='buy'?'C':'V'}${t.source==='backfill'?'<i title="Recuperado via REST">↺</i>':''}</b></div>`).join('')}</div>`:'<div class="flow-empty"><strong>Aguardando negócios</strong><span>As execuções reais aparecerão aqui.</span></div>';
    $('#flowCaption').textContent='C = agressor comprador · V = vendedor · ↺ recuperação';
  }
}
function analysisData(){
  if(replayTimer)return replayData;
  if(analysisCacheVersion!==captureVersion){analysisCache=flow.exportData();analysisCacheVersion=captureVersion;}
  return analysisCache;
}
function persistFlowPrefs(){save('flow-v3',flowPrefs);captureVersion++;renderDirty=true;}
function optionList(values,current,label=v=>v){return values.map(v=>`<option value="${v}" ${v===current?'selected':''}>${label(v)}</option>`).join('');}
function renderAnalysisToolbar(){
  const bar=$('#analysisToolbar');bar.hidden=['paper','health'].includes(analysisTab);
  const descriptions={footprint:'VOLUME EXECUTADO',profile:'VOLUME POR PREÇO',cvd:'AGRESSÃO ACUMULADA',heatmap:'LIQUIDEZ OBSERVADA'};
  let controls='';
  if(analysisTab==='footprint')controls=`<label>Período <select data-flow-pref="bucketSeconds" aria-label="Período do footprint">${optionList([15,30,60,300],flowPrefs.bucketSeconds,v=>v<60?v+'s':v/60+'m')}</select></label><label>Colunas <select data-flow-pref="columns" aria-label="Colunas do footprint">${optionList([3,6,9,12],flowPrefs.columns)}</select></label><button data-price-page="1" title="Mostrar preços mais altos" aria-label="Mostrar preços mais altos">↑</button><button data-price-page="-1" title="Mostrar preços mais baixos" aria-label="Mostrar preços mais baixos">↓</button><button data-price-page="0" title="Centralizar preços">Centrar</button>`;
  if(analysisTab==='cvd')controls=`<label>Intervalo <select data-flow-pref="cvdBucket" aria-label="Intervalo do CVD">${optionList([1,5,15,60],flowPrefs.cvdBucket,v=>v+'s')}</select></label><label>Janela <select data-flow-pref="cvdWindow" aria-label="Janela do CVD">${optionList([60,180,600,0],flowPrefs.cvdWindow,v=>v?v/60+' min':'Captura')}</select></label><label>Zero <select data-flow-pref="cvdBaseline" aria-label="Base do CVD">${optionList(['session','window'],flowPrefs.cvdBaseline,v=>v==='session'?'Captura':'Janela')}</select></label>`;
  if(analysisTab==='heatmap')controls=`<label>Janela <select data-flow-pref="heatWindow" aria-label="Janela do heatmap">${optionList([60,180,600],flowPrefs.heatWindow,v=>v/60+' min')}</select></label><span class="toolbar-micro">20 níveis/lado · ≤1 snapshot/s</span>`;
  if(analysisTab==='profile')controls=`<span class="toolbar-micro">POC global da captura · 12 faixas de maior volume</span>`;
  bar.innerHTML=`<span class="analysis-kicker">${descriptions[analysisTab]??''}</span><div class="analysis-controls">${controls}<button data-open-flow-settings title="Configurar agregação e filtros">${icon('settings')}<span>Configurar</span></button></div>`;
}
function renderAnalysis(){
  if(analysisTab==='paper'){renderAccount();return;}
  if(analysisTab==='health'){renderHealth();return;}
    const s=displaySummary(),step=flowStep(),data=analysisData(),until=replayTimer?replayTime:Date.now();
  if(analysisTab==='cvd'){
    if(!cvdView)cvdView=new FlowCanvas($('#cvdCanvas'),{type:'cvd',formatPrice:v=>formatPrice(v,currentAsset()),formatQuantity:quantity});
    const end=until,from=flowPrefs.cvdWindow?end-flowPrefs.cvdWindow*1000:-Infinity;
    const result=computeCVD(data.trades,{bucketSeconds:flowPrefs.cvdBucket,until,from,resetAt:flowPrefs.cvdBaseline==='window'&&Number.isFinite(from)?from:null,gaps:replayTimer?(data.gaps??[]):captureGaps});
    cvdView.setData(result);
    $('#cvdStats').innerHTML=`<div><span>CVD · ${flowPrefs.cvdBaseline==='window'?'zero na janela':'zero na captura retida'}</span><strong class="${result.finalValue>=0?'positive':'negative'}">${currentAsset().capabilities.trades?(result.finalValue>=0?'+':'')+quantity(result.finalValue):'—'} <small>${esc(unit())}</small></strong></div><span class="visual-scope">${scopeNote(s)}</span><span class="chart-legend-key"><i class="cvd-key"></i>CVD<i class="delta-key"></i>Delta / intervalo</span>`;
    $('#cvdCaption').textContent=currentAsset().capabilities.trades===false?'Negócios classificados indisponíveis para este contrato.':`Passe o cursor ou clique para fixar a inspeção. ${s.truncated?'Base parcial: os primeiros trades já saíram da janela retida. ':''}Soma de compras − vendas observadas; espaços sem eventos não confirmam continuidade.`;
    return;
  }
  if(analysisTab==='heatmap'){
    if(!heatmapView)heatmapView=new FlowCanvas($('#heatmapCanvas'),{type:'heatmap',formatPrice:v=>formatPrice(v,currentAsset()),formatQuantity:quantity});
    const heatStep=Number(flowPrefs.heatSteps[viewSymbol()])||Math.max(currentAsset().tickSize,step/10);
    const result=computeHeatmap(data.books,{step:heatStep,until,windowSeconds:flowPrefs.heatWindow,maxRows:48,maxColumns:180});
    heatmapView.setData(result);
    $('#heatmapStats').innerHTML=`<div><span>Liquidez por preço</span><strong>${result.columns.length} <small>amostras visíveis</small></strong></div><span class="visual-scope">${rangeMarkup(result.from,result.to)} · ${formatPrice(heatStep,currentAsset())} ${currency()}/faixa</span><span class="chart-legend-key"><i class="bid-key"></i>Bid<i class="ask-key"></i>Ask<span class="heat-gradient"></span>Maior volume</span>`;
    $('#heatmapCaption').textContent=`${replayTimer?'REPLAY · ':''}Liquidez L2 agregada; não representa ordens individuais. Cor mais intensa = maior quantidade. Clique para inspecionar; intervalos sem amostras ficam vazios.`;
    return;
  }
  const intro=`<div class="flow-analysis-heading"><span>${scopeNote(s)}</span><span class="flow-step">${esc(unit())} · passo ${formatPrice(step,currentAsset())} ${currency()}</span></div>`;
  if(!s.count){$(`#${analysisTab}Surface`).innerHTML=`${intro}<div class="analysis-empty">${icon('chart')}<div><strong>Aguardando os primeiros negócios.</strong><p>${currentAsset().capabilities.trades?`Os painéis são formados pelos negócios classificados recebidos de ${esc(sourceLabel())}.`:'O coletor não disponibiliza negócios classificados para este contrato.'}</p></div></div>`;return;}
  let analytics=displayFlow();
  if(replayTimer||analysisTab==='footprint'&&flowPrefs.minSize>0){analytics=createFlowStore();analytics.reset(viewSymbol(),viewMetadata());for(const t of data.trades)if(t.time<=until&&(analysisTab!=='footprint'||t.size>=flowPrefs.minSize))analytics.addTrade(t);}
  if(analysisTab==='footprint'){
    const fp=analytics.getFootprint({step,bucketSeconds:flowPrefs.bucketSeconds,columns:flowPrefs.columns}),columns=fp.columns,all=fp.priceLevels??[];
    const lastPrice=replayTimer?replayFlow.getAt(replayTime).price:s.lastPrice;
    let center=all.findIndex(p=>p<=lastPrice);if(center<0)center=Math.floor(all.length/2);
    const maxStart=Math.max(0,all.length-flowPrefs.rows),base=Math.max(0,Math.min(maxStart,center-Math.floor(flowPrefs.rows/2)));
    const start=Math.max(0,Math.min(maxStart,base+footprintOffset)),levels=all.slice(start,start+flowPrefs.rows);
    const max=Math.max(1e-9,...columns.flatMap(c=>c.levels.map(l=>l.volume)));
    $('#footprintSurface').innerHTML=`${intro}<div class="footprint-scroll"><table class="footprint-table" style="min-width:${Math.max(400,82+columns.length*130)}px"><thead><tr><th>Preço / ${currency()}</th>${columns.map(c=>`<th>${timeMarkup(c.time)}<small>VENDA × COMPRA</small></th>`).join('')}</tr></thead><tbody>${levels.map(price=>`<tr><th>${formatPrice(price,currentAsset())}</th>${columns.map(c=>{const l=c.levels.find(x=>Math.abs(x.price-price)<step/10);if(!l)return '<td class="empty-cell">—</td>';const buyImb=flowPrefs.imbalance>0&&l.buy>0&&l.sell>0&&l.buy/l.sell>=flowPrefs.imbalance,sellImb=flowPrefs.imbalance>0&&l.sell>0&&l.buy>0&&l.sell/l.buy>=flowPrefs.imbalance;return `<td tabindex="0" data-fp-inspect="${c.time}|${price}" title="${quantity(l.sell)} vendidos × ${quantity(l.buy)} comprados · clique para detalhes" style="background:rgba(${l.delta>=0?'36,201,160':'236,101,115'},${.035+l.volume/max*.18})"><span class="negative ${sellImb?'imbalance-sell':''}">${quantity(l.sell)}</span><i>×</i><span class="positive ${buyImb?'imbalance-buy':''}">${quantity(l.buy)}</span></td>`;}).join('')}</tr>`).join('')}</tbody><tfoot><tr><th>Delta / ${flowPrefs.bucketSeconds}s</th>${columns.map(c=>`<td class="${c.delta>=0?'positive':'negative'}">${c.delta>=0?'+':''}${quantity(c.delta)}</td>`).join('')}</tr></tfoot></table></div><div class="analysis-note">${levels.length} de ${all.length} faixas · ${flowPrefs.minSize?`trades ≥ ${quantity(flowPrefs.minSize)} ${esc(unit())} · `:''}${flowPrefs.imbalance?`destaque ≥${flowPrefs.imbalance}× no mesmo preço, com ambos os lados >0`:'destaque de desequilíbrio desligado'} · clique em uma célula</div>`;
    flowInspect={fp,step};
  }else{
    const p=analytics.getProfile({step,limit:12}),max=Math.max(1e-9,...p.levels.map(l=>l.volume));
    $('#profileSurface').innerHTML=`${intro}<div class="profile-layout"><div class="profile-levels">${p.levels.map(l=>`<div class="profile-row ${l.price===p.poc?'poc':''}"><span>${formatPrice(l.price,currentAsset())}</span><div class="profile-track"><b style="width:${l.sell/max*100}%" class="sell"></b><b style="width:${l.buy/max*100}%" class="buy"></b></div><strong>${quantity(l.volume)}</strong>${l.price===p.poc?'<em>POC</em>':'<em></em>'}</div>`).join('')}</div><div class="profile-metrics"><div><span>POC da captura</span><strong>${formatPrice(p.poc,currentAsset())}</strong></div><div><span>Volume capturado</span><strong>${quantity(s.totalVolume)} ${esc(unit())}</strong></div><div><span>Delta acumulado</span><strong class="${s.delta>=0?'positive':'negative'}">${s.delta>=0?'+':''}${quantity(s.delta)}</strong></div><small>Perfil sem o filtro mínimo do footprint.<br>POC usa todas as faixas.</small></div></div>`;
  }
}
function renderHealth(){
  const s=feedStatus,rec=flow.getRecording(),a=assetMap.get(currentSymbol),remote=currentProvider==='rithmic';
  const items=[['Conexão',statusLabel()],['Fonte',a.sourceLabel],['Contrato',a.displaySymbol+' · '+a.exchange],['Assinatura',s.subscriptionConfirmed?'Confirmada':'Aguardando confirmação'],['Último heartbeat',ageText(s.lastHeartbeatAt)],['Último negócio recebido',ageText(s.lastTradeAt)],['Snapshot / atualização L2',ageText(liveBook.time)],['Reconexões',String(s.reconnects??0)],['Lacunas reportadas',s.gaps==null?'Não informado':String(s.gaps)],['Negócios sem agressor',String(s.unclassifiedTrades??0)],['Histórico OHLCV',historyUpdatedAt?displayTime.dateTime(historyUpdatedAt):a.capabilities.history?'Aguardando':'Indisponível'],['Negócios retidos',compact(rec.trades)],['Snapshots retidos',compact(rec.frames)]];
  $('#healthSurface').innerHTML=`<div class="health-header"><span>${esc(a.sourceLabel)} · ${esc(a.displaySymbol)}</span><button id="configureFeed">${icon('settings')} Conexão</button><button id="reconnectFeed">${icon('replay')} Reconectar</button></div><div class="health-grid">${items.map(([k,v])=>`<div><span>${k}</span><strong>${esc(v)}</strong></div>`).join('')}</div><p class="health-note">${s.error?`<b>${esc(s.error)}</b> · `:''}${historyError?`Histórico: ${esc(historyError)} · `:''}${historyCoverage?`${esc(historyCoverageNotice(historyCoverage))} · `:''}${remote?'Contratos e assinatura confirmados pelo coletor QHOS. A indicação ao vivo exige dados recentes do contrato ativo. Negócios sem lado agressor são excluídos do fluxo classificado; o contador acima indica essa cobertura.':'Heartbeat mede atividade recebida. L2 sem sequência auditável: reconexão exige novo snapshot.'} Fluxo capturado no ativo selecionado. MBO e fila continuam vinculados a arquivos importados. <button id="dataMethod">Entender os dados</button></p>`;
  $('#configureFeed').onclick=()=>contractManager.openConnection();$('#reconnectFeed').onclick=()=>{feed.reconnect();if(currentProvider==='coinbase')loadChart(true);};$('#dataMethod').onclick=openDataMethod;
}
function statusLabel(){if(collectorAuto?.getSnapshot().manualOnly)return 'Aguardando conexão manual';if(collectorAuto?.getSnapshot().active||(currentProvider==='rithmic'&&qhosBridge.getSnapshot().retrying))return 'Conectando ao coletor';return ({connecting:'Conectando',waiting:'Aguardando dados',live:'Ao vivo',stale:'Dados atrasados',offline:'Desconectado',reconnecting:'Reconectando',error:'Falha de conexão'})[feedStatus.state]??'Aguardando confirmação';}
function renderStatus(){
  const pending=!!feedStatus.selectionPending,label=statusLabel(),live=feedStatus.state==='live'&&!pending,color=collectorAuto?.getSnapshot().manualOnly&&qhosBridge.getSnapshot().state!=='error'?'#607080':collectorAuto?.getSnapshot().active?'#dba75c':live?'var(--green)':['offline','error'].includes(feedStatus.state)?'var(--red)':'#dba75c';
  if(collectorAuto?.getSnapshot().manualOnly&&$('#chartMessage').textContent==='Conectando ao coletor')$('#chartMessage').textContent='Configure a conexão manual com o coletor.';
  $('#feedBadgeText').textContent=replayTimer?'REPLAY':pending?'ALTERANDO ATIVO':label.toUpperCase();$('#feedBadge').style.color=replayTimer?'#dba75c':color;$('#feedBadge>span').style.background=replayTimer?'#dba75c':color;$('#healthDot').style.background=color;$('#connectionDot').style.background=currentProvider==='rithmic'||collectorAuto?.getSnapshot().active?collectorLightPresentation(collectorLinkLight(qhosBridge.getSnapshot(),collectorAuto.getSnapshot())).color:color;renderCollectorLights();
  $('#connectionDetails').textContent=`${collectorAuto?.getSnapshot().active?'Coletor QHOS':currentProvider==='rithmic'?'Rithmic'+(qhosEnvironment==='test'?' Test':''):'Coinbase'} · ${label}`;
  $('#statusDetail').textContent=pending?'Aguardando confirmação da nova seleção':`Heartbeat ${ageText(feedStatus.lastHeartbeatAt)}${currentProvider==='coinbase'?` · ${feedStatus.unrecovered??0} IDs pendentes`:` · ${feedStatus.unclassifiedTrades??0} negócios sem agressor`}`;
  const rec=flow.getRecording();$('#recordingStatus').textContent=`● ${compact(rec.trades)} negócios · captura local`;
  const candleOrigin=currentProvider==='rithmic'?`${historyCandles.length?'candles recebidos do coletor':'negócios recebidos'}${candles.at(-1)?.partial===true||candles.at(-1)?.captured===true?' · último candle parcial':''}`:'candle atual parcial';
  $('#chartProvenance').textContent=replayTimer?`REPLAY · ${replayData?.imported?'arquivo importado, origem não autenticada':displayTime.sessionName(replayData)??'captura salva'}`:pending?`Exibindo ${displaySymbol()} até a confirmação da troca`:`${sourceLabel()} · ${displaySymbol()} · ${candleOrigin}${(feedStatus.unclassifiedTrades??0)>0?' · agressor incompleto':''}`;
  $('#chartProvenance').title=!replayTimer&&currentProvider==='rithmic'?(currentAsset().capabilities.history?'OHLCV informado pelo coletor, inclusive o período corrente. Novos negócios disparam consultas com intervalo mínimo de 2 segundos; sem novos negócios, reconciliação a cada 60 segundos. Períodos posteriores usam os negócios recebidos, sem somar volumes ao histórico.':'Candles formados somente pelos negócios classificados recebidos. A cobertura começa nesta captura do navegador.'):'Candles fechados consultados na fonte. O candle atual usa os negócios classificados capturados. Histórico reconciliado a cada 60 segundos quando disponível.';
  $('#chartSource').innerHTML=`<span></span>${replayTimer?'Replay':pending?'Troca pendente':label}`;$('#chartClock').textContent=displayTime.time(replayTimer?replayTime:Date.now(),{zone:true});
}
function checkAlerts(a){if(!a.updatedAt||!Number.isFinite(a.price))return;let changed=false;for(const alert of alerts){if(alert.triggered||alert.symbol!==a.symbol)continue;if(alert.condition==='above'?a.price>=alert.price:a.price<=alert.price){alert.triggered=true;changed=true;toast(`${a.symbol}: ${alert.condition==='above'?'acima de':'abaixo de'} ${formatPrice(alert.price,a)}.`);}}if(changed){save('alerts',alerts);updateAlertBadge();}}
function updateRiskMarkers(){
  if(replayTimer){chart.setMarkers?.([]);return;}
  const markers=[];for(const p of account.positions.filter(x=>x.symbol===currentSymbol).slice(0,2)){
    markers.push({price:p.price,label:`Entrada ${p.side==='buy'?'C':'V'} · ${quantity(p.qty)}`,color:'#7e9be9'});
    if(p.stopPrice)markers.push({price:p.stopPrice,label:p.stopActive?'Stop acionado':'Stop simulado',color:'#ec6573'});
    if(p.targetPrice)markers.push({price:p.targetPrice,label:'Alvo simulado',color:'#24c9a0'});
  }chart.setMarkers?.(markers);
}
function renderAccount(){
  const openPnl=totalPnl();$('#accountEquity').textContent=money(account.balance+openPnl);
  $('#realizedPnl').textContent=signedMoney(account.realized);$('#realizedPnl').className=account.realized>=0?'positive':'negative';
  $('#unrealizedPnl').textContent=signedMoney(openPnl);$('#unrealizedPnl').className=openPnl>=0?'positive':'negative';
  $('#feesPaid').textContent=money(account.feesPaid);$('#orderCount').textContent=account.orders.length;
  const list=tradeTab==='positions'?account.positions:tradeTab==='orders'?account.orders:account.history;
  if(!list.length){const messages={positions:['Planeje a entrada. Defina a saída.','Simule com stop, alvo e custos usando saldo virtual.'],orders:['Nenhuma ordem pendente.','O capital e a taxa estimada são reservados até a execução.'],history:['Suas decisões, com os custos visíveis.','Entradas, saídas e resultados líquidos aparecerão aqui.']};$('#positionsContent').innerHTML=`<div class="empty-positions"><span class="empty-position-icon">${icon(tradeTab==='history'?'clock':'layers')}</span><div><strong>${messages[tradeTab][0]}</strong><p>${messages[tradeTab][1]}</p></div></div>`;updateRiskMarkers();return;}
  $('#positionsContent').innerHTML=`<table class="position-table"><thead><tr><th>Ativo</th><th>Lado / Qtd.</th><th>${tradeTab==='orders'?'Limite':'Preço'}</th><th>${tradeTab==='history'?'Taxa da execução':'Stop / alvo'}</th><th>${tradeTab==='positions'?'Líquido estimado':'Status'}</th><th></th></tr></thead><tbody>${list.slice(0,50).map(p=>{const a=assetMap.get(p.symbol),pnl=positionPnl(p)-(p.entryFeeRemaining??0)-feeFor(markPrice(p)*p.qty,p.feeBps??workspace.feeBps);const protection=`<span class="risk-values"><b class="negative">${p.stopPrice?formatPrice(p.stopPrice,a):'—'}</b><b class="positive">${p.targetPrice?formatPrice(p.targetPrice,a):'—'}</b></span>`;return `<tr><td>${p.symbol}${tradeTab==='positions'&&p.symbol!==currentSymbol?'<small class="position-idle">proteção pausada</small>':''}</td><td><span class="${p.side==='buy'?'positive':'negative'}">${p.side==='buy'?'C':'V'}</span> ${quantity(p.qty)}</td><td>${formatPrice(p.price,a)}</td><td>${tradeTab==='history'?money(p.fee??0):protection}</td><td class="${tradeTab==='positions'?(pnl>=0?'positive':'negative'):''}">${tradeTab==='positions'?signedMoney(pnl):tradeTab==='orders'?'Pendente':esc(p.status??'Executada')}${tradeTab==='positions'&&p.stopActive?'<small class="negative">Stop aguardando liquidez</small>':''}</td><td><div class="position-actions">${tradeTab==='positions'?`<button class="risk-edit" data-edit-protection="${p.id}" title="Editar stop e alvo">${icon('shield')}<span>Proteção</span></button><button class="close-position" data-close-position="${p.id}">Encerrar</button>`:tradeTab==='orders'?`<button class="close-position" data-cancel-order="${p.id}">Cancelar</button>`:''}</div></td></tr>`;}).join('')}</tbody></table><div class="paper-caption">${replayTimer?'REPLAY: execuções e proteções pausadas. ':''}Posições: resultado estimado ao bid/ask, com taxas de entrada e saída; preenchimento real da simulação depende da profundidade. Proteções locais no ativo selecionado.</div>`;
  updateRiskMarkers();
}
function openModal(title,body,{wide=false,eyebrow='QHOS TERMINAL',name=''}={}){
  if(navigation.drawerOpen)navigation.setDrawer(false,true);
  lastFocused=document.activeElement;activeModal=name;refreshOrderForm=null;
  $('#floatingMenu').hidden=true;$('#modal').classList.toggle('wide',wide);
  $('#modalTitle').textContent=title;$('#modalEyebrow').textContent=eyebrow;$('#modalBody').innerHTML=body;
  $('#modalBackdrop').hidden=false;document.body.style.overflow='hidden';hydrateIcons($('#modal'));
  requestAnimationFrame(()=>{$('input:not([disabled]), textarea, select, button', $('#modalBody'))?.focus();});
}
function closeModal(){
  contractManager?.close?.();
  $('#modalBackdrop').hidden=true;document.body.style.overflow='';activeModal='';refreshOrderForm=null;lastFocused?.focus?.();
}
$('#closeModal').addEventListener('click',closeModal);
$('#modalBackdrop').addEventListener('click',e=>{if(e.target===$('#modalBackdrop'))closeModal();});


function openSearch(){contractManager.open();}

function openIndicators(){
  const options=[['ema20','Média móvel exponencial','EMA · 20 períodos','#dba75c'],['ema50','Média móvel exponencial','EMA · 50 períodos','#6e92dc'],['rsi','Índice de força relativa','RSI · 14 períodos','#aa8fdd'],['volume','Volume de negociação','Volume por candle','#24c9a0']];
  openModal('Enxergue o mercado por outro ângulo.',`<div class="modal-body-padding"><p class="modal-description">Indicadores calculados sobre os candles da fonte selecionada. O candle corrente contém apenas os negócios capturados.</p>${options.map(([key,title,subtitle,color])=>`<button class="indicator-option" data-indicator="${key}" role="switch" aria-checked="${chartOptions[key]}"><div><span class="indicator-color" style="background:${color}"></span><div><strong>${title}</strong><p>${subtitle}</p></div></div><span class="toggle ${chartOptions[key]?'on':''}"></span></button>`).join('')}<button class="primary-button" id="indicatorsDone">Pronto, voltar ao gráfico</button></div>`,{name:'indicators',eyebrow:'INDICADORES TÉCNICOS'});
  $$('[data-indicator]').forEach(button=>button.addEventListener('click',()=>{const key=button.dataset.indicator;chartOptions[key]=!chartOptions[key];button.setAttribute('aria-checked',String(chartOptions[key]));$('.toggle',button).classList.toggle('on',chartOptions[key]);chart.setOptions(chartOptions);updateLegend();persistPrefs();}));
  $('#indicatorsDone').addEventListener('click',closeModal);
}
function showChartTypes(){
  const button=$('#chartTypeButton'),rect=button.getBoundingClientRect(),menu=$('#floatingMenu');
  if(!menu.hidden){menu.hidden=true;return;}
  menu.innerHTML=[['candles','Candles','candles'],['line','Linha','line'],['area','Área','area']].map(([type,name,ic])=>`<button data-chart-type="${type}" class="${type===chartOptions.type?'active':''}">${icon(ic)}${name}${type===chartOptions.type?icon('check'):''}</button>`).join('');
  menu.style.left=`${Math.min(rect.left,window.innerWidth-180)}px`;menu.style.top=`${rect.bottom+7}px`;menu.hidden=false;
}
$('#floatingMenu').addEventListener('click',e=>{const b=e.target.closest('[data-chart-type]');if(!b)return;chartOptions.type=b.dataset.chartType;chart.setOptions(chartOptions);persistPrefs();$('#floatingMenu').hidden=true;});
document.addEventListener('pointerdown',e=>{if(!e.target.closest('#floatingMenu,#chartTypeButton'))$('#floatingMenu').hidden=true;});

function setTool(tool,notify=true){
  selectedTool=tool;chart.setTool(tool);$$('[data-tool]').forEach(b=>{b.classList.toggle('active',b.dataset.tool===tool);b.setAttribute('aria-pressed',String(b.dataset.tool===tool));});
  const hint=$('#drawingHint');hint.hidden=tool==='crosshair';hint.textContent=tool==='trend'?'Clique no início e no fim da linha · Esc para sair':'Clique no preço para marcar uma linha · Esc para sair';
  if(notify&&tool!=='crosshair')setTimeout(()=>{hint.hidden=true;},6500);
}

function openAlert(initialPrice=null){
  const a=currentAsset();if(!Number.isFinite(a.price)){toast('Aguarde a primeira cotação do ativo.','error');return;}
  openModal(`Alerta para ${a.displaySymbol}`,`<form id="alertForm" class="modal-body-padding"><p class="modal-description">Receba um aviso nesta página quando o preço do ativo atingir sua condição.</p><div class="form-row"><div class="form-field"><label for="alertCondition">Quando o preço estiver</label><select id="alertCondition"><option value="above">Acima de</option><option value="below">Abaixo de</option></select></div><div class="form-field"><label for="alertPrice">Preço (${a.currency})</label><input id="alertPrice" type="number" min="0.00001" step="any" value="${Number((Number.isFinite(initialPrice)?initialPrice:a.price*1.005).toFixed(a.priceDecimals??2))}" required></div></div><div class="form-info">${icon('info')}Os alertas são locais: a página precisa estar aberta e a conexão com o feed, ativa.</div><div class="form-error" id="alertError" hidden></div><button class="primary-button" type="submit">${icon('bell-plus')} Criar alerta</button></form>`,{name:'alert',eyebrow:'NO SEU RADAR'});
  $('#alertForm').addEventListener('submit',e=>{e.preventDefault();const price=Number($('#alertPrice').value);if(!(price>0&&Number.isFinite(price))){$('#alertError').textContent='Informe um preço válido.';$('#alertError').hidden=false;return;}alerts.push({id:uid(),symbol:a.symbol,instrument:instrumentMetadata(a),price,condition:$('#alertCondition').value,triggered:false});save('alerts',alerts);updateAlertBadge();closeModal();toast(`Alerta criado para ${a.symbol}.`);});
}
function updateAlertBadge(){$('.notification-dot').hidden=!alerts.length;}
function openAlerts(){
  openModal('Seus alertas de preço.',`<div class="modal-body-padding"><p class="modal-description">Acompanhe condições sobre os preços da fonte selecionada, enquanto esta página estiver aberta.</p><div id="alertsList"></div><button id="addAlertFromList" class="primary-button">${icon('plus')} Novo alerta para ${esc(displaySymbol())}</button></div>`,{name:'alerts',eyebrow:'FIQUE DE OLHO'});
  const render=()=>{$('#alertsList').innerHTML=alerts.map(a=>`<div class="alert-card">${icon(a.triggered?'check-circle':'bell')}<div><strong>${esc(a.instrument?.symbol??displaySymbol(a.symbol))} · ${a.condition==='above'?'Acima de':'Abaixo de'} ${formatPrice(a.price,assetMap.get(a.symbol)??a.instrument)}</strong><p>${a.triggered?'Condição atingida':assets.some(item=>item.symbol===a.symbol)?'Ativo · aguardando condição':'Pausado · ativo fora da seleção'}</p></div><button data-delete-alert="${esc(a.id)}" aria-label="Excluir alerta">${icon('trash')}</button></div>`).join('')||'<div class="no-content">Você ainda não criou alertas.</div>';};render();
  $('#alertsList').addEventListener('click',e=>{const b=e.target.closest('[data-delete-alert]');if(!b)return;alerts=alerts.filter(a=>a.id!==b.dataset.deleteAlert);save('alerts',alerts);updateAlertBadge();render();});
  $('#addAlertFromList').addEventListener('click',openAlert);
}


function openPortfolio(){
  const pnl=totalPnl();
  openModal('Sua conta de paper trading.',`<div class="modal-body-padding"><div class="order-summary"><div><span>Patrimônio virtual</span><strong>${money(account.balance+pnl)}</strong></div><div><span>Capital em posições</span><strong>${money(marginUsed())}</strong></div><div><span>Reservado em ordens</span><strong>${money(reservedMargin())}</strong></div><div><span>Resultado em aberto</span><strong class="${pnl>=0?'positive':'negative'}">${signedMoney(pnl)}</strong></div><div><span>Resultado realizado</span><strong class="${account.realized>=0?'positive':'negative'}">${signedMoney(account.realized)}</strong></div></div><p class="modal-description">${account.positions.length} posição(ões) aberta(s) · ${account.orders.length} ordem(ns) pendente(s). Suas operações ficam salvas neste navegador. Marcação no bid/ask recebido; ativos sem cotação usam o preço de entrada até a conexão.</p><button class="primary-button" id="portfolioTrade">${icon('plus')} Fazer uma operação simulada</button></div>`,{name:'portfolio',eyebrow:'SALDO VIRTUAL · SEM CORRETORA'});
  $('#portfolioTrade').addEventListener('click',()=>openOrder('buy'));
}
function openNotes(){
  const noteSymbol=viewSymbol(),notes=load('notes',{}),text=typeof notes?.[noteSymbol]==='string'?notes[noteSymbol]:'';
  openModal(`Seu olhar sobre ${displaySymbol()}.`,`<div class="modal-body-padding"><p class="modal-description">Registre hipóteses, níveis de interesse e aprendizados. Suas notas ficam salvas neste navegador.</p><textarea id="notesText" class="notes-textarea" placeholder="O que você está observando neste mercado?" aria-label="Notas de análise" maxlength="10000"></textarea><button class="primary-button" id="saveNotes">${icon('check')} Salvar análise</button></div>`,{name:'notes',eyebrow:'DIÁRIO DE MERCADO'});
  $('#notesText').value=text;$('#saveNotes').addEventListener('click',()=>{const updated=notes&&typeof notes==='object'&&!Array.isArray(notes)?notes:{};updated[noteSymbol]=$('#notesText').value;save('notes',updated);closeModal();toast('Análise salva neste navegador.');});
}

function openOrder(side='buy',draft={}){
  if(!paperSupported()){toast('O simulador atual atende aos pares spot Coinbase. A contabilidade de futuros ainda não está habilitada.','error');return;}
  if(replayTimer){toast('Encerre o replay para operar com o livro ao vivo.','error');return;}
  const a=currentAsset();let orderSide=side;
  if(!Number.isFinite(a.price)){toast('Aguarde a primeira cotação do ativo.','error');return;}
  const initialQty=a.symbol==='BTCUSD'?.01:a.symbol==='ETHUSD'?.1:1;
  openModal(`Negociar ${a.symbol}`,`<form id="orderForm" class="modal-body-padding"><div class="order-mode-note">${icon('shield')} SALDO VIRTUAL · nenhuma ordem enviada</div><div class="side-picker"><button type="button" data-side="buy" class="${side==='buy'?'active':''}">Comprar</button><button type="button" data-side="sell" class="${side==='sell'?'active':''}">Vender</button></div><div class="form-row"><div class="form-field"><label for="orderType">Tipo de ordem</label><select id="orderType"><option value="market">A mercado · IOC</option><option value="limit">Limitada · GTC local</option></select></div><div class="form-field"><label for="orderQuantity">Quantidade (${esc(unit())})</label><input id="orderQuantity" type="number" min="0.00000001" max="1000000" step="any" value="${initialQty}" required></div></div><div class="form-row" id="limitPriceField" hidden><div class="form-field"><label for="orderPrice">Preço limite (USD)</label><input id="orderPrice" type="number" min="0.000001" max="1000000000000" step="any" value="${draft.price??a.price}"></div></div><div class="bracket-heading"><span>PROTEÇÃO VINCULADA · OPCIONAL</span><button type="button" id="bracketPreset">Stop 1% / alvo 2%</button></div><div class="form-row"><div class="form-field"><label for="orderStop">Stop (USD)</label><input id="orderStop" type="number" min="0.000001" max="1000000000000" step="any" placeholder="Sem stop"></div><div class="form-field"><label for="orderTarget">Alvo (USD)</label><input id="orderTarget" type="number" min="0.000001" max="1000000000000" step="any" placeholder="Sem alvo"></div></div><div class="order-summary"><div><span>Melhor bid / ask</span><strong id="orderQuote">—</strong></div><div><span>Preenchimento estimado</span><strong id="orderFill">—</strong></div><div><span>Preço médio estimado</span><strong id="orderAverage">—</strong></div><div><span>Capital + reserva + taxa</span><strong id="orderTotal">—</strong></div><div><span>Saldo virtual disponível</span><strong id="orderAvailable">—</strong></div><div><span>Taxa na entrada · ${num(workspace.feeBps,1)} bps</span><strong id="orderFee">—</strong></div><div><span>Perda no stop, com taxas estimadas</span><strong id="orderRisk">Sem stop</strong></div></div><div class="form-info">Stop a mercado; alvo limitado. OCO local: uma saída reduz a outra pela quantidade encerrada. Proteções e ordens pendentes funcionam com a página aberta, feed atualizado e ativo selecionado. Taxa também na saída. Sem modelo de fila, impacto ou latência.</div><div class="form-error" id="orderError" hidden></div><button class="primary-button" id="submitOrder" type="submit">Simular ordem</button></form>`,{name:'order',eyebrow:'PAPER TRADING · DADOS REAIS'});
  if(draft.type==='limit')$('#orderType').value='limit';
  const readOrder=()=>{
    const qty=Number($('#orderQuantity').value),type=$('#orderType').value,limit=type==='limit'?Number($('#orderPrice').value):undefined;
    const valid=Number.isFinite(qty)&&qty>0&&qty<=1e6&&(type!=='limit'||Number.isFinite(limit)&&limit>0&&limit<=1e12);
    const fill=valid?simulateMarketFill(orderSide,qty,liveBook,{limitPrice:limit}):null;
    const reference=limit??fill?.averagePrice??(orderSide==='buy'?a.ask:a.bid)??a.price;
    let bracket=validateBrackets({side:orderSide,entryPrice:reference,stopPrice:$('#orderStop').value,targetPrice:$('#orderTarget').value});if(bracket.valid&&fill?.filled)bracket=validateBrackets({side:orderSide,entryPrice:fill.averagePrice,stopPrice:$('#orderStop').value,targetPrice:$('#orderTarget').value});
    const capital=fill?entryCapital(fill,{remaining:type==='limit'?fill.remaining:0,limitPrice:limit,feeBps:workspace.feeBps}):null;
    return {qty,type,limit,valid,fill,reference,bracket,capital};
  };
  const refresh=()=>{
    if(!$('#orderForm'))return;const x=readOrder(),{fill,capital,bracket}=x;
    $('#limitPriceField').hidden=x.type!=='limit';$('#orderQuote').textContent=`${formatPrice(liveBook.bids[0]?.[0],a)} / ${formatPrice(liveBook.asks[0]?.[0],a)}`;
    $('#orderFill').textContent=fill?`${quantity(fill.filled)} de ${quantity(x.qty)} ${esc(unit())}`:'—';$('#orderAverage').textContent=formatPrice(fill?.averagePrice,a);
    $('#orderTotal').textContent=capital?money(capital.total):'—';$('#orderAvailable').textContent=money(availableBalance());$('#orderFee').textContent=capital?money(capital.entryFee)+(capital.pendingFee?` + ${money(capital.pendingFee)} reservados`:''):'—';
    const riskQty=x.type==='limit'?x.qty:(fill?.filled??0);
    $('#orderRisk').textContent=bracket.stopPrice&&x.valid?money(Math.abs(x.reference-bracket.stopPrice)*riskQty+feeFor(x.reference*riskQty,workspace.feeBps)+feeFor(bracket.stopPrice*riskQty,workspace.feeBps)):'Sem stop';
    $('#orderError').hidden=bracket.valid;$('#orderError').textContent=bracket.error??'';
    $('#submitOrder').textContent=!healthyBook()?'Aguardando livro atualizado':!x.valid?'Informe quantidade e preço':!bracket.valid?'Ajuste stop e alvo':`${orderSide==='buy'?'Comprar':'Vender'} ${a.symbol} · Simulado`;
    $('#submitOrder').disabled=!healthyBook()||!x.valid||!bracket.valid;$('#submitOrder').classList.toggle('danger',orderSide==='sell');
  };
  refreshOrderForm=refresh;refresh();
  $$('[data-side]',$('#modalBody')).forEach(b=>b.onclick=()=>{orderSide=b.dataset.side;$$('[data-side]',$('#modalBody')).forEach(x=>x.classList.toggle('active',x===b));refresh();});
  for(const id of ['orderQuantity','orderPrice','orderType','orderStop','orderTarget'])$(`#${id}`).oninput=refresh;
  $('#bracketPreset').onclick=()=>{const x=readOrder(),sign=orderSide==='buy'?1:-1;$('#orderStop').value=Number((x.reference*(1-sign*.01)).toFixed(4));$('#orderTarget').value=Number((x.reference*(1+sign*.02)).toFixed(4));refresh();};
  $('#orderForm').onsubmit=e=>{
    e.preventDefault();if(!healthyBook())return showError('Livro desatualizado. Aguarde a reconexão.');
    const x=readOrder();if(!x.valid||!x.bracket.valid)return showError(x.bracket.error??'Informe quantidade e preço válidos.');
    if(x.capital.total>freeEquity()+1e-6)return showError('Saldo virtual insuficiente, incluindo as taxas e reservas.');
    if(x.type==='market'&&!x.fill.filled)return showError('Sem liquidez visível para esta execução.');
    const orderId=uid(),trade={id:orderId,orderId,symbol:a.symbol,qty:x.qty,price:x.limit??x.fill.averagePrice,side:orderSide,type:x.type,time:Date.now(),feeBps:workspace.feeBps,stopPrice:x.bracket.stopPrice,targetPrice:x.bracket.targetPrice};
    if(x.fill.filled)executeTrade({...trade,qty:x.fill.filled,price:x.fill.averagePrice,fills:x.fill.levels.map(({price,size})=>({price,size})),role:'entry'},x.fill.complete?'Entrada executada':'Entrada parcial');
    if(x.type==='limit'&&x.fill.remaining>1e-10)account.orders.unshift({...trade,qty:x.fill.remaining,price:x.limit});
    persistAccount();tradeTab=x.fill.filled?'positions':'orders';updateTradeTabs();closeModal();openAnalysisScreen('paper');renderDirty=true;
    toast(x.fill.filled?`${quantity(x.fill.filled)} ${esc(unit())} simulados · taxa ${money(x.capital.entryFee)}${x.fill.remaining>1e-10?(x.type==='market'?' · restante cancelado.':' · restante pendente.'):'.'}`:'Ordem limitada local criada com reserva de taxas.');
  };
}
function executeTrade(trade,status='Entrada executada'){
  const fee=feeFor(trade.qty*trade.price,trade.feeBps??workspace.feeBps);
  const execution={...trade,fills:Array.isArray(trade.fills)?trade.fills.map(({price,size})=>({price,size})):undefined,role:'entry',feeBps:trade.feeBps??workspace.feeBps,entryFeeRemaining:fee,orderId:trade.orderId??trade.id,id:uid(),stopActive:false};
  account.balance-=fee;account.realized-=fee;account.feesPaid+=fee;account.positions.unshift(execution);
  account.history.unshift({...execution,id:uid(),fee,netPnl:-fee,status});account.history=account.history.slice(0,200);return execution;
}
function consumeBook(book,side,levels){const target=side==='buy'?book.asks:book.bids;for(const fill of levels){const level=target.find(l=>l[0]===fill.price);if(level)level[1]=Math.max(0,level[1]-fill.size);}}
function applyExit(p,fill,reason='manual'){
  const result=computeExit(p,fill,{feeBps:workspace.feeBps});if(!result.qty)return result;
  account.balance+=result.balanceDelta;account.realized+=result.balanceDelta;account.feesPaid+=result.exitFee;
  account.positions=account.positions.filter(x=>x.id!==p.id);if(result.remaining>0)account.positions.unshift({...p,qty:result.remaining,entryFeeRemaining:result.remainingEntryFee});
  account.orders=account.orders.filter(o=>!p.orderId||(o.orderId??o.id)!==p.orderId);
  const label=reason==='stop'?'Stop':reason==='target'?'Alvo':'Saída';
  account.history.unshift({...p,id:uid(),side:p.side==='buy'?'sell':'buy',qty:result.qty,price:result.averagePrice,time:Date.now(),fills:fill.levels.map(({price,size})=>({price,size})),role:'exit',executionReason:reason,fee:result.exitFee,allocatedEntryFee:result.allocatedEntryFee,grossPnl:result.grossPnl,netPnl:result.netPnl,status:`${label}${result.complete?(reason==='manual'?' executada':' executado'):' parcial'} · ${signedMoney(result.netPnl)}`});account.history=account.history.slice(0,200);return result;
}
function processPaperOrders(){
  if(!healthyBook()||!account.positions.length&&!account.orders.length)return;
  const book={...liveBook,bids:liveBook.bids.map(l=>[...l]),asks:liveBook.asks.map(l=>[...l])};let changed=false,exits=0,entries=0;
  for(const p of [...account.positions]){
    if(p.symbol!==currentSymbol)continue;const signal=getExitSignal(p,liveBook);if(!signal)continue;
    if(signal.reason==='stop'&&!p.stopActive){p.stopActive=true;changed=true;}
    const count=account.orders.length;account.orders=account.orders.filter(o=>!p.orderId||(o.orderId??o.id)!==p.orderId);if(count!==account.orders.length)changed=true;
    const fill=simulateMarketFill(signal.side,p.qty,book,{limitPrice:signal.limitPrice});
    if(fill.filled){applyExit(p,fill,signal.reason);consumeBook(book,signal.side,fill.levels);changed=true;exits++;}
  }
  for(const order of [...account.orders]){
    if(order.symbol!==currentSymbol)continue;const fill=simulateMarketFill(order.side,order.qty,book,{limitPrice:order.price});if(!fill.filled)continue;
    const capital=entryCapital(fill,{remaining:fill.remaining,limitPrice:order.price,feeBps:order.feeBps});if(capital.total>freeEquity()+orderReserve(order)+1e-6)continue;
    account.orders=account.orders.filter(o=>o.id!==order.id);executeTrade({...order,qty:fill.filled,price:fill.averagePrice,time:Date.now(),fills:fill.levels.map(({price,size})=>({price,size})),role:'entry'},fill.complete?'Limitada executada':'Limitada parcial');
    if(fill.remaining>1e-10)account.orders.push({...order,qty:fill.remaining});consumeBook(book,order.side,fill.levels);changed=true;entries++;
  }
  if(changed){persistAccount();renderDirty=true;if(exits)toast('Proteção executada na simulação. Consulte o histórico e as taxas.');else if(entries)toast('Ordem limitada preenchida na simulação.');}
}
function closePosition(id){
  if(currentProvider!=='coinbase'){toast('Escolha a fonte Coinbase na busca para retomar as posições virtuais spot.','error');return;}
  const p=account.positions.find(p=>p.id===id);if(!p)return;if(p.symbol!==currentSymbol){if(!assets.some(a=>a.symbol===p.symbol)){toast('Adicione '+displaySymbol(p.symbol)+' à lista Coinbase para retomar esta posição.','error');return;}setSymbol(p.symbol);toast('Ativo selecionado. Aguarde o livro e clique em Encerrar novamente.');return;}
  if(!healthyBook()){toast('Aguarde um livro atualizado e encerre o replay para fechar a posição.','error');return;}
  const fill=simulateMarketFill(p.side==='buy'?'sell':'buy',p.qty,liveBook);if(!fill.filled){toast('Sem liquidez visível para encerrar.','error');return;}
  const result=applyExit(p,fill);persistAccount();renderAccount();renderDirty=true;toast(`Resultado líquido da saída: ${signedMoney(result.netPnl)}${!result.complete?' · restante aberto.':'.'}`);
}
function editProtection(id){
  if(currentProvider!=='coinbase'){toast('Escolha a fonte Coinbase na busca para editar as proteções virtuais spot.','error');return;}
  const p=account.positions.find(x=>x.id===id);if(!p)return;
  if(p.symbol!==currentSymbol){if(!assets.some(a=>a.symbol===p.symbol)){toast('Adicione '+displaySymbol(p.symbol)+' à lista Coinbase para retomar esta posição.','error');return;}setSymbol(p.symbol);toast('Ativo selecionado. Aguarde a conexão e abra Proteção novamente.');return;}
  if(!healthyBook()){toast('Aguarde um livro atualizado para editar a proteção.','error');return;}
  if(p.stopActive){toast('Stop já acionado. O restante aguarda liquidez para sair.','error');return;}
  openModal(`Proteção de ${p.symbol}`,`<form id="protectionForm" class="modal-body-padding"><p class="modal-description">${p.side==='buy'?'Compra':'Venda'} de ${quantity(p.qty)} · entrada ${formatPrice(p.price,currentAsset())}. Os níveis são validados em relação ao bid/ask atual, permitindo proteger lucro. Campos vazios removem a proteção.</p><div class="form-row"><div class="form-field"><label for="positionStop">Stop (USD)</label><input id="positionStop" type="number" min="0.000001" max="1000000000000" step="any" value="${p.stopPrice??''}" placeholder="Sem stop"></div><div class="form-field"><label for="positionTarget">Alvo (USD)</label><input id="positionTarget" type="number" min="0.000001" max="1000000000000" step="any" value="${p.targetPrice??''}" placeholder="Sem alvo"></div></div><div class="form-info">Stop usa a profundidade disponível, sem garantir o preço marcado. Alvo usa uma ordem limitada local. Ao sair, qualquer entrada restante vinculada é cancelada.</div><div id="protectionError" class="form-error" hidden></div><button class="primary-button" type="submit">Salvar proteção</button></form>`,{name:'protection',eyebrow:'STOP / ALVO · OCO LOCAL'});
  $('#protectionForm').onsubmit=e=>{e.preventDefault();const current=account.positions.find(x=>x.id===id);if(!current||current.stopActive){closeModal();toast('A posição mudou durante a edição. Confira a lista.','error');return;}if(!healthyBook()){$('#protectionError').hidden=false;$('#protectionError').textContent='Aguarde o livro atualizado.';return;}const result=validateBrackets({side:current.side,entryPrice:current.side==='buy'?liveBook.bids[0][0]:liveBook.asks[0][0],stopPrice:$('#positionStop').value,targetPrice:$('#positionTarget').value});if(!result.valid){$('#protectionError').hidden=false;$('#protectionError').textContent=result.error;return;}Object.assign(current,{stopPrice:result.stopPrice,targetPrice:result.targetPrice});persistAccount();closeModal();renderDirty=true;toast('Proteção atualizada.');};
}
function cancelOrder(id){account.orders=account.orders.filter(o=>o.id!==id);persistAccount();renderAccount();toast('Ordem local cancelada.');}
function updateTradeTabs(){$$('[data-trade-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tradeTab===tradeTab));}

function sessionName(symbol=currentSymbol){return `${assetMap.get(symbol)?.displaySymbol??symbol} · ${new Date().toLocaleDateString('pt-BR',{day:'2-digit',month:'short',timeZone:'UTC'})} ${utcDisplay.time(Date.now())} UTC`;}
function snapshotLive({id=captureSessionId,name=captureName,kind='auto'}={}){
  return createSession(flow.exportData(),{...sourceMetadata(assetMap.get(currentSymbol)),id,name,kind,createdAt:captureCreatedAt,timeframe:currentTimeframe,historyCandles,settings:{flow:flowPrefs,chart:chartOptions},diagnostics:feedStatus,gaps:[...captureGaps,...(activeGap?[{...activeGap,to:Date.now()}]:[])]});
}
function snapshotVisible(name){
  if(!replayTimer)return snapshotLive({id:uid(),name:name||sessionName(),kind:'saved'});
  return createSession(replayData,{id:uid(),name:name||replayData.name||sessionName(),kind:replayData.imported?'imported':'saved',timeframe:replayData.timeframe,historyCandles:replayData.historyCandles,settings:replayData.settings,diagnostics:replayData.diagnostics,gaps:replayData.gaps});
}
async function refreshSessionCatalog(){
  try{sessionCatalog=await listSessions();$('#sessionCount').textContent=String(sessionCatalog.length);if(activeModal==='sessions')renderSessionList();}
  catch(e){storageError=e.message;if(activeModal==='sessions')renderSessionList();}
}
async function checkpointSession(){
  const rec=flow.getRecording();if(!workspace.autoSave||!rec.trades&&!rec.frames||lastSavedVersion===captureVersion)return;
  const version=captureVersion,id=captureSessionId;let data;
  try{data=snapshotLive();sessionBusy=true;await saveSession(data);if(id===captureSessionId){lastSavedVersion=version;lastSavedAt=Date.now();}storageError='';await refreshSessionCatalog();}
  catch(e){storageError=e.message;}finally{sessionBusy=false;renderStorageStatus();}
}
function renderStorageStatus(){const el=$('#storageStatus');el.classList.toggle('storage-error',!!storageError);el.textContent=storageError?'Falha ao salvar':sessionBusy?'Salvando…':!workspace.autoSave?'Autosave desligado':lastSavedAt?'Salvo '+displayTime.time(lastSavedAt,{zone:true}):'Autosave ativo';el.title=storageError||'Captura salva neste navegador. Abra Sessões para revisar e exportar.';}
function renderSessionList(){
  if(!$('#sessionList'))return;
  $('#sessionStorageNote').textContent=storageError||`${sessionCatalog.length} / 50 sessões · neste navegador · sem sincronização na nuvem`;
  $('#sessionStorageNote').classList.toggle('negative',!!storageError);
  $('#sessionList').innerHTML=sessionCatalog.length?sessionCatalog.map(s=>`<article class="session-card"><div class="session-symbol">${esc(s.instrument?.symbol??s.symbol.replace('USD',''))}</div><div class="session-info"><strong>${esc(displayTime.sessionName(s))}</strong><span>${s.imported?'Arquivo importado · origem não autenticada':esc((s.kind==='auto'?'Checkpoint automático · ':'Captura salva · ')+(s.sourceLabel??'Coinbase'))} · ${esc(s.timeframe)}</span><small>${displayTime.dateTime(s.updatedAt)} · ${compact(s.recording?.trades??0)} trades · ${compact(s.recording?.frames??0)} snapshots${s.recording?.truncated?' · janela parcial':''}</small></div><div class="session-actions"><button data-session-action="replay" data-session-id="${esc(s.id)}" class="session-play" title="Reproduzir sessão">${icon('play')}<span>Replay</span></button><button data-session-action="rename" data-session-id="${esc(s.id)}" title="Renomear sessão" aria-label="Renomear ${esc(displayTime.sessionName(s))}">${icon('note')}</button><button data-session-action="export" data-session-id="${esc(s.id)}" title="Exportar JSON" aria-label="Exportar ${esc(displayTime.sessionName(s))}">${icon('download')}</button><button data-session-action="delete" data-session-id="${esc(s.id)}" ${s.id===captureSessionId&&workspace.autoSave?'disabled':''} title="${s.id===captureSessionId&&workspace.autoSave?'Checkpoint da captura ativa':'Excluir sessão'}" aria-label="Excluir ${esc(displayTime.sessionName(s))}">${icon('trash')}</button></div></article>`).join(''):'<div class="session-empty">'+icon('bookmark')+'<strong>Sua biblioteca começa com esta captura.</strong><p>Salve os negócios e snapshots para estudar depois, ou importe uma sessão QHOS em JSON.</p></div>';
}
async function openSessions(){
  const defaultName=replayTimer?(replayData.name??sessionName()):sessionName();
  const visibleName=displayTime.sessionName({...(replayTimer&&replayData.name?replayData:{symbol:displaySymbol(),createdAt:Date.now()}),name:defaultName});
  openModal('Volte ao momento que importa.',`<div class="modal-body-padding"><p class="modal-description">Guarde a captura, reveja o fluxo e compare suas leituras. Os arquivos contêm trades, snapshots, candles disponíveis e diagnóstico da fonte.</p><form id="saveSessionForm" class="session-save-form"><label for="sessionNameInput" class="sr-only">Nome da sessão</label><input id="sessionNameInput" maxlength="100" placeholder="Nome da sessão" value="${esc(visibleName)}"><button type="submit" class="session-save-button">${icon('bookmark')} Salvar ${replayTimer?'replay':'captura'}</button></form><div class="session-list-heading"><span id="sessionStorageNote">Carregando biblioteca…</span><label class="import-session-button" for="importSessionFile">${icon('plus')} Importar JSON<input id="importSessionFile" type="file" accept=".json,application/json"></label></div><div id="sessionImportStatus" class="form-error" role="status" hidden></div><div id="sessionList" class="session-list"></div><p class="settings-help session-retention">Cada captura guarda até 20.000 trades e 600 snapshots; sessões longas conservam a janela mais recente. O checkpoint ocorre a cada 10 s. Exporte os arquivos que deseja manter fora deste navegador.</p></div>`,{wide:true,name:'sessions',eyebrow:'BIBLIOTECA DE SESSÕES · QHOS v3'});
  renderSessionList();
  $('#saveSessionForm').onsubmit=async e=>{e.preventDefault();const button=$('button',e.currentTarget);button.disabled=true;try{const enteredName=$('#sessionNameInput').value.trim(),data=snapshotVisible(enteredName===visibleName?defaultName:enteredName);if(!data.trades.length&&!data.books.length)throw new Error('Aguarde os primeiros eventos para salvar.');await saveSession(data);storageError='';await refreshSessionCatalog();toast('Sessão salva para reprodução.');}catch(error){if($('#sessionImportStatus')){$('#sessionImportStatus').hidden=false;$('#sessionImportStatus').textContent=error.message;}}finally{if(button.isConnected)button.disabled=false;}};
  $('#importSessionFile').onchange=async e=>{const file=e.target.files?.[0];if(!file)return;const status=$('#sessionImportStatus');status.hidden=false;status.classList.remove('negative');status.textContent='Validando arquivo…';try{if(file.size>30*1024*1024)throw new Error('O arquivo ultrapassa o limite de 30 MB.');const parsed=parseSession(await file.text());parsed.id=uid();parsed.kind='imported';parsed.name=parsed.name||file.name.replace(/\.json$/i,'');await saveSession(parsed);await refreshSessionCatalog();if(status.isConnected)status.textContent='Importação concluída. O arquivo foi validado; a origem de mercado não é autenticada.';toast('Sessão importada. Use Replay para inspecionar.');}catch(error){if(status.isConnected){status.textContent=error.message;status.classList.add('negative');}}finally{e.target.value='';}};
  $('#sessionList').onclick=async e=>{
    const b=e.target.closest('[data-session-action]');if(!b)return;const id=b.dataset.sessionId,action=b.dataset.sessionAction;
    try{const data=await getSession(id);if(!data)throw new Error('Esta sessão não está mais disponível.');
      if(action==='replay'){closeModal();beginReplay(data);}
      if(action==='export')downloadSession(data);
      if(action==='rename'){const visibleName=displayTime.sessionName(data);openModal('Nomeie esta sessão.',`<form id="renameSessionForm" class="modal-body-padding"><div class="form-field"><label for="renameSessionName">Nome da sessão</label><input id="renameSessionName" maxlength="100" required value="${esc(visibleName)}"></div><button class="primary-button" type="submit">Salvar nome</button></form>`,{name:'session-rename'});$('#renameSessionForm').onsubmit=async event=>{event.preventDefault();try{const enteredName=$('#renameSessionName').value,nextName=enteredName===visibleName?data.name:enteredName;await renameSession(id,nextName);if(id===captureSessionId)captureName=nextName;await openSessions();toast('Sessão renomeada.');}catch(error){toast(error.message,'error');}};}
      if(action==='delete'){openModal('Excluir esta sessão?',`<div class="modal-body-padding"><p class="modal-description">${esc(displayTime.sessionName(data))} será removida deste navegador. Uma cópia exportada poderá ser importada novamente.</p><button class="primary-button danger" id="confirmDeleteSession">Excluir da biblioteca</button><button class="source-link" id="cancelDeleteSession">Manter sessão</button></div>`,{name:'session-delete'});$('#cancelDeleteSession').onclick=openSessions;$('#confirmDeleteSession').onclick=async()=>{try{await deleteSession(id);await openSessions();toast('Sessão excluída.');}catch(error){toast(error.message,'error');}};}
    }catch(error){toast(error.message,'error');}
  };
  await refreshSessionCatalog();
}
function startReplay(){if(replayTimer){stopReplay();return;}const rec=flow.getRecording();if(rec.trades<1){toast('Aguarde alguns negócios para formar uma captura.','error');return;}beginReplay(snapshotLive({kind:'saved'}));}
function beginReplay(data){
  navigation.show('terminal',{pane:'chart'});
  if(replayTimer)stopReplay(false);const rec=data.recording;if(!Number.isFinite(rec?.from)||!Number.isFinite(rec?.to)){toast('A sessão não contém eventos reproduzíveis.','error');return;}
  replayAsset=data.instrument?.provider==='rithmic'?registerInstrument(data.instrument,{environment:data.environment,register:false}):assetMap.get(data.symbol);
  if(!replayAsset){toast('Metadados do contrato ausentes na sessão.','error');return;}
  replayData=data;replayFlow=createFlowStore();replayFlow.reset(data.symbol,{instrument:data.instrument,sourceLabel:data.sourceLabel,environment:data.environment});for(const t of data.trades)replayFlow.addTrade(t);for(const b of data.books)replayFlow.addBook(b);
  replayFrom=rec.from;replayTo=rec.to;replayTime=replayFrom;replayPaused=replayTo<=replayFrom;footprintOffset=0;
  $('#chartMessage').hidden=true;$('#replayControl').hidden=false;$('#replayButton').classList.add('active');$('#replayPause').innerHTML=icon(replayPaused?'play':'pause');$('#replayScrub').disabled=replayTo<=replayFrom;document.body.classList.add('is-replaying');
  $$('[data-timeframe]').forEach(b=>{b.classList.toggle('active',b.dataset.timeframe===data.timeframe);b.setAttribute('aria-pressed',String(b.dataset.timeframe===data.timeframe));});
  replayTimer=setInterval(()=>{if(!replayPaused){replayTime=Math.min(replayTo,replayTime+200*replaySpeed);if(replayTime>=replayTo){replayPaused=true;$('#replayPause').innerHTML=icon('play');$('#replayPause').setAttribute('aria-label','Retomar replay');}paintReplay();}},200);
  executionContextKey='';executionLayer?.clearSelection();chart.setDrawings?.([]);paintReplay();chart.resetView();toast(data.imported?'Replay de arquivo importado. Origem não autenticada; operações pausadas.':'Replay iniciado. O feed continua capturando; operações ficam pausadas.');
}
function paintReplay(){if(!replayTimer)return;renderChartIdentity();candles=composeCandles(replayTime,replayData.trades);chart.setData(candles,currentAsset());chart.setMarkers?.([]);refreshExecutions();updateOHLC(candles.at(-1));updateLegend();$('#chartTimeframe').textContent=replayData.timeframe??currentTimeframe;$('#replayTime').textContent=`REPLAY ${displayTime.time(replayTime,{zone:true})}`;$('#replayScrub').value=String(Math.round((replayTime-replayFrom)/(replayTo-replayFrom||1)*1000));renderFlow();renderAnalysis();renderDetails();renderStatus();}
function stopReplay(notify=true){executionContextKey='';executionLayer?.clearSelection();clearInterval(replayTimer);replayTimer=null;replayPaused=false;replayData=null;replayFlow=null;replayAsset=null;$('#replayControl').hidden=true;$('#replayButton').classList.remove('active');document.body.classList.remove('is-replaying');$('#chartTimeframe').textContent=currentTimeframe;$$('[data-timeframe]').forEach(b=>{b.classList.toggle('active',b.dataset.timeframe===currentTimeframe);b.setAttribute('aria-pressed',String(b.dataset.timeframe===currentTimeframe));});chartDrawingSymbol='';renderChartIdentity();paintChart();chart.resetView();if(!historyCandles.length)loadChart();renderDirty=true;if(notify)toast('Visualização ao vivo restaurada.');}
function downloadSession(data){downloadBlob(new Blob([JSON.stringify(data)],{type:'application/json'}),`QHOS-${data.symbol}-${new Date(data.updatedAt??Date.now()).toISOString().replace(/[:.]/g,'-')}.json`);toast('Sessão exportada em JSON.');}
function exportSession(){try{const data=snapshotVisible();if(!data.trades.length&&!data.books.length){toast('Ainda não há eventos para exportar.','error');return;}downloadSession(data);}catch(e){toast(e.message,'error');}}
function downloadBlob(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function openDataMethod(){
  if(currentAsset()?.provider==='rithmic'){
    const a=currentAsset(),state=qhosBridge.getSnapshot();
    openModal('Origem e cobertura deste contrato.',`<div class="modal-body-padding"><div class="source-card"><div><strong>${esc(sourceLabel())} · ${esc(a.displaySymbol)}</strong><p>${esc(a.exchange)} · ${esc(a.currency)} · ${esc(a.quantityUnit)}${a.expiry?' · vencimento '+esc(expiryText(a.expiry)):''}</p></div></div><div class="method-list"><section><h3>Seleção controlada pelo QHOS</h3><p>O dashboard solicita a lista de contratos e o ativo do gráfico ao coletor. A tela adota a seleção confirmada pelo serviço. Durante uma troca, os dados anteriores permanecem identificados até a confirmação; mensagens de uma assinatura anterior são descartadas.</p><p>${replayTimer?'Visualização de uma sessão salva.':state.connected?'O serviço QHOS confirmou a conexão. A atualidade é avaliada pelos eventos recebidos do contrato ativo.':'O coletor QHOS está desconectado. Configure a conexão para consultar os contratos disponíveis.'}</p></section><section><h3>Gráfico, livro e fluxo</h3><p>Cotações, negócios classificados e snapshots L2 vêm do coletor. Footprint, perfil, delta, CVD e estudos são calculados sobre os eventos retidos. Histórico depende da disponibilidade informada para cada contrato.</p><p>Negócios sem lado agressor conhecido atualizam o último preço, mas ficam fora dos marcadores, do fluxo direcional e do candle corrente calculado localmente. O painel Feed mostra essa contagem. Não é feita classificação automática como compra ou venda.</p></section><section><h3>Precisão e gravação</h3><p>O contrato determina moeda, tick e unidade. O timestamp original em nanossegundos é preservado quando fornecido; o gráfico usa coordenadas em milissegundos. A captura local guarda até 20.000 negócios e 600 snapshots de até 20 níveis por lado, no máximo um por segundo.</p><p>Replay conserva a identificação e o ambiente da sessão salva. Arquivos importados não têm origem autenticada.</p></section><section><h3>Ferramentas disponíveis</h3><p>Consulta de fila e lifecycle MBO usam arquivos importados. Receber cotações ou L2 não habilita ordens individuais. O simulador e a carteira experimental atuais atendem aos pares spot Coinbase; contabilidade e execução de futuros ainda não estão habilitadas.</p></section></div><button id="methodConnection" class="primary-button">Configurar conexão QHOS</button></div>`,{wide:true,name:'method',eyebrow:'FONTE E CAPACIDADES'});
    $('#methodConnection').onclick=()=>contractManager.openConnection();return;
  }
  openModal('De onde vêm os dados.',`<div class="modal-body-padding"><div class="source-card"><span class="live-mark"></span><div><strong>Coinbase Exchange · mercado spot</strong><p>BTC-USD, ETH-USD e SOL-USD. Fonte pública específica desta bolsa.</p></div></div><div class="method-list"><section><h3>Preço, candles e profundidade</h3><p>Cotações e negócios via WebSocket. Histórico OHLCV via REST; 4h agrega candles de 1h em UTC. O candle corrente usa apenas os trades capturados, e seu volume é parcial. Candles fechados são reconciliados a cada 60 segundos.</p><p>O DOM mostra volumes agregados por preço (L2), atualizado por lotes de 50 ms na fonte. Não identifica ordens individuais ou posição na fila.</p></section><section><h3>Footprint, delta e perfil</h3><p>Calculados apenas sobre negócios retidos nesta captura. Compra e venda representam o agressor, invertendo o lado maker informado pela Coinbase. Delta = volume comprador − volume vendedor. CVD acumula esse delta desde a base escolhida. O heatmap mostra quantidades do livro L2 por preço e tempo; colunas agrupadas usam média das amostras, com intervalos sem snapshots vazios. VWAP é ponderado pela quantidade; POC é a faixa com maior volume.</p></section><section><h3>Execuções sobre o gráfico</h3><p>Círculos verdes representam agressão compradora no ask; vermelhos representam agressão vendedora no bid. Cada negócio usa seu preço e timestamp da bolsa, armazenado em milissegundos. Marcadores próximos são agrupados por pixels; ao ampliar, os eventos se separam. Eventos no mesmo preço e milissegundo podem ser inspecionados individualmente na lista.</p><p>Triângulos representam apenas execuções da conta virtual, com horário no fuso selecionado. Novas operações guardam cada preço preenchido; registros antigos sem essa informação são identificados pelo preço médio. Alterações do livro e ordens pendentes não geram marcas de execução. O replay mostra apenas os negócios da sessão até o cursor, sem misturar a conta virtual.</p></section><section><h3>Integridade e replay</h3><p>IDs de trade e heartbeat ajudam a detectar lacunas; tentamos recuperar negócios via REST e exibimos os IDs ainda não recuperados. O L2 público não fornece uma sequência auditável. Após reconectar, o livro aguarda um novo snapshot.</p><p>A janela de captura retém até 20.000 negócios e 600 snapshots de até 20 níveis por lado, no máximo 1 por segundo. O salvamento automático grava um checkpoint a cada 10 segundos neste navegador. A biblioteca preserva sessões entre visitas; é possível exportar e importar JSON. Ao recarregar ou trocar de ativo, a captura ao vivo recomeça, e a anterior pode ser aberta na biblioteca. O replay usa apenas eventos até o cursor e o histórico fechado disponível no arquivo. Arquivos importados têm validação estrutural, sem autenticar sua origem.</p></section><section><h3>Paper trading</h3><p>Saldo inicial de US$ 100 mil, sem alavancagem. Taxa padrão da simulação: 10 bps por execução (0,10%), editável nas configurações; não representa a tabela real da Coinbase. A taxa fica fixada na ordem ou posição. Entradas anteriores à v3 não recebem cobrança retroativa. Ordens usam até 40 níveis do livro observado; não há modelo de fila, impacto ou latência. Stop usa mercado, alvo usa limite, com saídas parciais e proteção vinculada. Ordens e proteções só são avaliadas com a página aberta, este ativo selecionado e livro atualizado; são pausadas no replay. Preços de stop não são garantidos.</p></section><section><h3>Integração QHOS / futuros</h3><p>A busca permite configurar a conexão ao coletor QHOS e consultar seu catálogo de contratos. O coletor precisa implementar a interface de comunicação do dashboard e confirmar as assinaturas. Selecionar a aba Rithmic, por si só, não ativa dados dessa fonte. Credenciais de corretora ficam no coletor.</p></section></div><a class="source-link" href="https://docs.cdp.coinbase.com/exchange/websocket-feed/channels" target="_blank" rel="noopener noreferrer">Documentação oficial da fonte ↗</a></div>`,{wide:true,name:'method',eyebrow:'ORIGEM, COBERTURA E LIMITES'});
}
function openMarkets(){contractManager.open();}

function applyWorkspace(){
  document.body.dataset.density=workspace.density;document.body.classList.toggle('high-contrast',workspace.contrast);
  document.body.classList.toggle('workspace-focus',workspace.mode==='focus');
  document.documentElement.style.setProperty('--analysis-height',`${workspace.panelHeight}px`);
  fitWorkspace();
  save('workspace-v3',workspace);
}
function openFlowSettings(){
  openModal('Ajuste a leitura do fluxo.',`<form id="flowSettingsForm" class="modal-body-padding"><p class="modal-description">Agregação e filtros alteram a leitura dos negócios observados. As configurações de preço são salvas por ativo.</p><div class="settings-section-label">FOOTPRINT & PERFIL · ${esc(displaySymbol())}</div><div class="form-row"><div class="form-field"><label for="flowStepInput">Faixa de preço (${currency()})</label><input id="flowStepInput" type="number" min="0.000001" max="1000000" step="any" value="${flowStep()}" required></div><div class="form-field"><label for="flowRows">Faixas visíveis</label><select id="flowRows">${optionList([8,12,20,40],flowPrefs.rows)}</select></div></div><div class="form-row"><div class="form-field"><label for="flowMinSize">Trade mínimo no footprint (${esc(unit())})</label><input id="flowMinSize" type="number" min="0" max="1000000" step="any" value="${flowPrefs.minSize}" required></div><div class="form-field"><label for="flowImbalance">Destaque de desequilíbrio</label><select id="flowImbalance">${optionList([0,2,3,4],flowPrefs.imbalance,v=>v?`${v}× no mesmo preço`:'Desligado')}</select></div></div><p class="settings-help">Comparação horizontal: compra ÷ venda, ou venda ÷ compra, na mesma faixa. Só destaca quando os dois lados têm volume. CVD e perfil continuam usando todos os negócios.</p><div class="settings-section-label">HEATMAP L2 · ${esc(displaySymbol())}</div><div class="form-row"><div class="form-field"><label for="heatStepInput">Faixa de preço (${currency()})</label><input id="heatStepInput" type="number" min="0.000001" max="1000000" step="any" value="${Number(flowPrefs.heatSteps[viewSymbol()])||Math.max(currentAsset().tickSize,flowStep()/10)}" required></div><div class="form-field"><label for="heatWindowInput">Janela de tempo</label><select id="heatWindowInput">${optionList([60,180,600],flowPrefs.heatWindow,v=>v/60+' minutos')}</select></div></div><p class="settings-help">Até 48 faixas, centradas na região observada, com escala de cor logarítmica. Até 20 níveis por lado e uma amostra por segundo; colunas agrupadas usam a média das amostras disponíveis.</p><div class="form-error" id="flowSettingsError" hidden></div><button class="primary-button" type="submit">Aplicar configurações</button></form>`,{name:'flow-settings',eyebrow:'GRANULARIDADE E FILTROS'});
  $('#flowSettingsForm').onsubmit=e=>{e.preventDefault();const step=Number($('#flowStepInput').value),heat=Number($('#heatStepInput').value),min=Number($('#flowMinSize').value);if(![step,heat].every(v=>Number.isFinite(v)&&v>=.000001&&v<=1e6)||!Number.isFinite(min)||min<0||min>1e6){$('#flowSettingsError').hidden=false;$('#flowSettingsError').textContent='Informe faixas positivas e um filtro válido.';return;}flowPrefs.steps[viewSymbol()]=step;flowPrefs.heatSteps[viewSymbol()]=heat;flowPrefs.rows=Number($('#flowRows').value);flowPrefs.minSize=min;flowPrefs.imbalance=Number($('#flowImbalance').value);flowPrefs.heatWindow=Number($('#heatWindowInput').value);footprintOffset=0;persistFlowPrefs();renderAnalysisToolbar();closeModal();renderAnalysis();toast('Leitura do fluxo atualizada.');};
}
function renderChartViewState(state){
  const caption=$('#chartCoverage'),overview=$('#chartOverview');
  if(!caption||!overview||!state)return;
  overview.hidden=state.mode!=='executions';
  let text=state.totalCandles?`${state.totalCandles} ${state.totalCandles===1?'candle recebido':'candles recebidos'}`:'Nenhum candle recebido para este período.';
  if(state.totalCandles&&Number.isFinite(state.from)&&Number.isFinite(state.to)){
    const stamp=value=>displayTime.dateTime(value,{seconds:false,year:false});
    text+=` · ${stamp(state.from)}${state.to!==state.from?' – '+stamp(state.to):''}`;
    if(state.totalCandles<6)text+=' · Poucos candles disponíveis neste período.';
  }
  if(!replayTimer&&historyCoverage)text+=' · '+historyCoverageNotice(historyCoverage);
  else if(candles.some(c=>c.partial))text+=' · Há candles parciais; cobertura do período inteiro não comprovada.';
  if(state.gapCount)text+=` · ${state.gapCount} ${state.gapCount===1?'intervalo sem candles':'intervalos sem candles'}; espaços compactados e sinalizados no eixo.`;
  if(state.mode==='executions')text=`Inspeção de negócios · janela de ${num(state.windowSeconds,2)} s · `+text;
  if(caption.textContent!==text)caption.textContent=text;
}
function restoreCandleView(){executionLayer?.clearSelection();chart.resetView();}
function applyDisplayTimeZone(zone){
  displayTime.setZone(zone);persistPrefs();
  // Presentation only: do not call loadChart, paintChart, setData or any aggregation here.
  chart.refreshTimeZone();renderChartViewState(chart.getViewState());cvdView?.refreshTimeZone();heatmapView?.refreshTimeZone();
  executionLayer?.refreshTimeZone();microstructurePanel.refreshTimeZone();
  refreshDisplayTimes();renderStatus();renderStorageStatus();
  if($('#replayTime')&&replayTimer)$('#replayTime').textContent=`REPLAY ${displayTime.time(replayTime,{zone:true})}`;
  if(activeModal==='sessions')renderSessionList();
  if(analysisTab==='health')renderHealth();
}
function openSettings(){
  openModal('Um workspace do seu jeito.',`<form id="workspaceForm" class="modal-body-padding"><div class="settings-section-label">ORGANIZAÇÃO E LEGIBILIDADE</div><div class="form-row"><div class="form-field"><label for="workspaceMode">Organização</label><select id="workspaceMode">${optionList(['balanced','flow','focus'],workspace.mode,v=>({balanced:'Equilibrado',flow:'Foco no fluxo',focus:'Foco no gráfico'})[v])}</select></div><div class="form-field"><label for="workspaceDensity">Densidade</label><select id="workspaceDensity">${optionList(['compact','comfortable'],workspace.density,v=>v==='compact'?'Compacta':'Confortável')}</select></div></div><label class="check-setting"><span>Contraste reforçado <small>Texto secundário e separadores mais visíveis</small></span><input id="workspaceContrast" type="checkbox" ${workspace.contrast?'checked':''}></label><label class="check-setting"><span>Grade do gráfico</span><input id="workspaceGrid" type="checkbox" ${chartOptions.grid?'checked':''}></label><div class="settings-section-label">HORÁRIO DO TERMINAL</div><div class="form-field"><label for="workspaceTimeZone">Fuso de exibição</label><select id="workspaceTimeZone">${optionList(['America/Sao_Paulo','UTC','America/New_York'],displayTime.getZone(),zone=>({'America/Sao_Paulo':'Brasília (padrão)',UTC:'UTC','America/New_York':'Nova York'})[zone])}</select></div><p class="settings-help">Aplica-se aos horários na tela. Gravações e exportações continuam em UTC.</p><div class="settings-section-label">CAPTURA E SESSÕES</div><label class="check-setting"><span>Salvar captura automaticamente <small>Checkpoint a cada 10 segundos neste navegador</small></span><input id="workspaceAutoSave" type="checkbox" ${workspace.autoSave?'checked':''}></label><p class="settings-help">A biblioteca conserva a janela retida de até 20.000 trades e 600 snapshots por sessão. Exporte JSON para levar a captura a outro navegador. Dados locais podem ser apagados pelo navegador.</p><div class="settings-section-label">CUSTOS DA SIMULAÇÃO</div><div class="form-row"><div class="form-field"><label for="workspaceFee">Taxa por execução (bps)</label><input id="workspaceFee" type="number" min="0" max="1000" step="0.1" value="${workspace.feeBps}" required></div><div class="fee-explainer"><strong>10 bps = 0,10%</strong><span>Cobrada na entrada e na saída</span></div></div><p class="settings-help">É uma hipótese editável, não a tabela de taxas da Coinbase. A taxa fica fixada em cada nova posição ou ordem. Posições anteriores mantêm seus valores; não há cobrança retroativa.</p><button class="source-link" type="button" id="settingsConnection">Conexão QHOS / Rithmic ↗</button><button class="source-link" type="button" id="settingsSource">Origem e metodologia dos dados ↗</button><div class="form-error" id="workspaceError" hidden></div><button class="primary-button" type="submit">Salvar preferências</button><button id="resetAccount" class="subtle-danger" type="button">Reiniciar conta virtual</button></form>`,{name:'settings',eyebrow:'CONFIGURAÇÕES · QHOS v3'});
  $('#workspaceTimeZone').onchange=e=>applyDisplayTimeZone(e.target.value);
  $('#settingsSource').onclick=openDataMethod;$('#settingsConnection').onclick=()=>contractManager.openConnection();
  $('#workspaceForm').onsubmit=e=>{e.preventDefault();const fee=Number($('#workspaceFee').value);if(!Number.isFinite(fee)||fee<0||fee>1000||$('#workspaceFee').value===''){$('#workspaceError').hidden=false;$('#workspaceError').textContent='A taxa deve ficar entre 0 e 1.000 bps.';return;}const mode=$('#workspaceMode').value;if(mode!==workspace.mode&&mode!=='focus')workspace.panelHeight=mode==='flow'?420:320;workspace.mode=mode;workspace.density=$('#workspaceDensity').value;workspace.contrast=$('#workspaceContrast').checked;workspace.autoSave=$('#workspaceAutoSave').checked;workspace.feeBps=fee;chartOptions.grid=$('#workspaceGrid').checked;chart.setOptions(chartOptions);persistPrefs();applyWorkspace();closeModal();renderDirty=true;toast('Preferências salvas.');};
  $('#resetAccount').onclick=()=>{openModal('Reiniciar o paper trading?',`<div class="modal-body-padding"><p class="modal-description">As posições, ordens, taxas e o histórico virtual serão apagados. O saldo retorna a US$ 100.000,00. Sessões, notas e alertas são mantidos.</p><button class="primary-button" id="confirmReset">Reiniciar conta virtual</button><button class="source-link" id="cancelReset">Manter minha conta</button></div>`,{name:'reset'});$('#confirmReset').onclick=()=>{Object.assign(account,{balance:100000,realized:0,feesPaid:0,positions:[],orders:[],history:[]});persistAccount();renderAccount();closeModal();toast('Conta virtual reiniciada.');};$('#cancelReset').onclick=openSettings;};
}
function openHelp(){openModal('Explore o terminal.',`<div class="modal-body-padding"><p class="modal-description">Arraste o gráfico para navegar e use a roda do mouse para ampliar. O botão direito abre ações no preço. Os círculos verdes e vermelhos mostram execuções reais no ask e no bid; triângulos mostram suas execuções paper. Clique para ver horário em milissegundos e ampliar o instante. Com o gráfico focado, E inspeciona uma execução e [ / ] navegam. No CVD e no heatmap, passe o cursor ou clique para fixar a inspeção. Arraste o separador acima das abas para ajustar a altura da análise.</p><div class="shortcut-list"><span>Buscar ativo</span><kbd>Ctrl / ⌘ + K</kbd><span>Cursor / tendência / horizontal</span><kbd>C / T / H</kbd><span>Alerta de preço</span><kbd>A</kbd><span>Biblioteca de sessões</span><kbd>S</kbd><span>Foco no gráfico</span><kbd>F</kbd><span>Explorar candles com canvas focado</span><kbd>← / →</kbd><span>Restaurar zoom</span><kbd>Duplo clique</kbd><span>Fechar janela / sair do foco</span><kbd>Esc</kbd></div><button class="primary-button" id="helpMethod">Entender os dados e a simulação</button></div>`,{name:'help',eyebrow:'QHOS TERMINAL v3'});$('#helpMethod').onclick=openDataMethod;}

function openChartContext(point){
  if(!(point.price>0))return;contextPoint=point;const menu=$('#floatingMenu');
  menu.innerHTML=`<div class="context-price">${formatPrice(point.price,currentAsset())} ${currency()}<small>${timeMarkup(point.time*1000,'dateTime')} · ${replayTimer?'replay':'gráfico'}</small></div><button data-context-action="line">${icon('horizontal')} Marcar nível</button><button data-context-action="alert">${icon('bell-plus')} Criar alerta neste preço</button><button data-context-action="buy" ${!paperSupported()?'disabled':''}>${icon('plus')} Rascunho de compra limitada</button><button data-context-action="sell" ${!paperSupported()?'disabled':''}>${icon('minus')} Rascunho de venda limitada</button>`;
  menu.style.left=`${Math.max(8,Math.min(point.clientX,innerWidth-255))}px`;menu.style.top=`${Math.max(8,Math.min(point.clientY,innerHeight-220))}px`;menu.hidden=false;
}
function inspectFootprint(key){
  if(!flowInspect)return;const [time,price]=key.split('|').map(Number),col=flowInspect.fp.columns.find(c=>c.time===time),level=col?.levels.find(l=>Math.abs(l.price-price)<flowInspect.step/10);if(!level)return;
  openModal(`${formatPrice(price,currentAsset())} ${currency()}`,`<div class="modal-body-padding"><p class="modal-description">${timeMarkup(time,'dateTime')} · ${flowPrefs.bucketSeconds}s · faixa de ${formatPrice(flowInspect.step,currentAsset())} ${currency()} · ${level.count} negócios que atendem ao filtro.</p><div class="cell-metrics"><div><span>Volume vendedor</span><strong class="negative">${quantity(level.sell)}</strong></div><div><span>Volume comprador</span><strong class="positive">${quantity(level.buy)}</strong></div><div><span>Delta · ${esc(unit())}</span><strong class="${level.delta>=0?'positive':'negative'}">${quantity(level.delta)}</strong></div><div><span>Volume total · ${esc(unit())}</span><strong>${quantity(level.volume)}</strong></div></div><p class="settings-help">Leitura dos dados observados neste momento da análise. Compra e venda indicam o lado agressor; a célula não representa liquidez disponível no livro.</p></div>`,{name:'footprint-inspection',eyebrow:'INSPEÇÃO DO FOOTPRINT'});
}
function toggleFocus(){workspace.mode=workspace.mode==='focus'?'balanced':'focus';if(workspace.mode==='focus')navigation.setPane('chart');document.body.classList.remove('fullscreen-chart');applyWorkspace();toast(workspace.mode==='focus'?'Foco no gráfico · F ou Esc para voltar.':'Workspace restaurado.');}
const bindings={globalSearch:()=>openSearch(),symbolSelect:()=>openSearch(),addAssetButton:()=>openSearch(),indicatorsButton:openIndicators,railIndicators:openIndicators,alertButton:()=>openAlert(),railAlert:()=>openAlert(),notificationButton:openAlerts,chartTypeButton:showChartTypes,notesButton:openNotes,settingsButton:openSettings,helpButton:openHelp,shortcutsButton:openHelp,profileButton:openPortfolio,exploreMarkets:openMarkets,newOrderButton:()=>openOrder('buy'),buyButton:()=>openOrder('buy'),sellButton:()=>openOrder('sell'),sessionsButton:openSessions,storageStatus:openSessions,analysisSettings:openFlowSettings,replayButton:startReplay,replayStop:()=>stopReplay(),replayPause:()=>{replayPaused=!replayPaused;if(!replayPaused&&replayTime>=replayTo)replayTime=replayFrom;$('#replayPause').innerHTML=icon(replayPaused?'play':'pause');$('#replayPause').setAttribute('aria-label',replayPaused?'Retomar replay':'Pausar replay');},zoomIn:()=>chart.zoom(1.3),zoomOut:()=>chart.zoom(1/1.3),resetChart:restoreCandleView,autoScale:restoreCandleView,chartOverview:restoreCandleView,clearDrawings:()=>{chart.clearDrawings();setTool('crosshair',false);toast('Desenhos removidos.');},expandButton:toggleFocus,captureButton:()=>{const a=document.createElement('a');a.download=`QHOS-${displaySymbol()}-${currentTimeframe}-${replayTimer?'REPLAY':currentProvider.toUpperCase()}.png`;a.href=chart.exportImage({subtitle:replayTimer?(replayData.imported?'REPLAY · arquivo importado · origem não autenticada':`REPLAY · ${sourceLabel()}`):`${sourceLabel()} · ${displaySymbol()} · dados observados`});a.click();toast('Imagem do gráfico salva.');},favoriteButton:()=>{},sortWatchlist:()=>{sortDirection=sortDirection===1?-1:1;renderWatchlist();},feedBadge:()=>openAnalysisScreen('health'),connectionDetails:()=>openAnalysisScreen('health'),exportSession};
for(const [id,handler] of Object.entries(bindings))$(`#${id}`)?.addEventListener('click',handler);
$('#watchlist').addEventListener('click',e=>{const b=e.target.closest('[data-symbol]');if(b)setSymbol(b.dataset.symbol);});
$('#tickerStrip').addEventListener('click',e=>{const b=e.target.closest('[data-symbol]');if(b)setSymbol(b.dataset.symbol);});
$$('[data-tool]').forEach(b=>b.addEventListener('click',()=>setTool(b.dataset.tool)));
$$('[data-timeframe]').forEach(b=>b.addEventListener('click',()=>{if(replayTimer)stopReplay(false);currentTimeframe=b.dataset.timeframe;captureVersion++;loadChart();persistPrefs();}));
$$('[data-trade-tab]').forEach(b=>b.addEventListener('click',()=>{tradeTab=b.dataset.tradeTab;updateTradeTabs();renderAccount();}));
$$('[data-analysis]').forEach(b=>b.addEventListener('click',()=>selectAnalysis(b.dataset.analysis)));
$$('[data-flow]').forEach(b=>b.addEventListener('click',()=>{flowTab=b.dataset.flow;$$('[data-flow]').forEach(x=>x.classList.toggle('active',x===b));renderFlow();}));
$$('[data-view]').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.view==='markets')openMarkets();else if(b.dataset.view==='portfolio')openPortfolio();else{closeModal();navigation.show('terminal',{pane:'chart'});}}));
$('#positionsContent').addEventListener('click',e=>{const c=e.target.closest('[data-close-position]'),x=e.target.closest('[data-cancel-order]'),p=e.target.closest('[data-edit-protection]');if(c)closePosition(c.dataset.closePosition);if(x)cancelOrder(x.dataset.cancelOrder);if(p)editProtection(p.dataset.editProtection);});
$('#analysisToolbar').addEventListener('change',e=>{const key=e.target.dataset.flowPref;if(!key)return;flowPrefs[key]=key==='cvdBaseline'?e.target.value:Number(e.target.value);footprintOffset=0;persistFlowPrefs();renderAnalysis();});
$('#analysisToolbar').addEventListener('click',e=>{if(e.target.closest('[data-open-flow-settings]'))openFlowSettings();const b=e.target.closest('[data-price-page]');if(b){const direction=Number(b.dataset.pricePage);footprintOffset=direction===0?0:footprintOffset-direction*Math.max(2,Math.floor(flowPrefs.rows/2));renderAnalysis();}});
$('.analysis-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)||!e.target.matches('[data-analysis]'))return;e.preventDefault();const tabs=$$('[data-analysis]'),index=tabs.indexOf(e.target),next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;selectAnalysis(tabs[next].dataset.analysis);tabs[next].focus();});
$('#footprintSurface').addEventListener('click',e=>{const cell=e.target.closest('[data-fp-inspect]');if(cell)inspectFootprint(cell.dataset.fpInspect);});
$('#footprintSurface').addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)&&e.target.matches('[data-fp-inspect]')){e.preventDefault();inspectFootprint(e.target.dataset.fpInspect);}});
$('#flowContent').addEventListener('click',e=>{const row=e.target.closest('[data-order-price]');if(row)openOrder(row.dataset.orderSide,{type:'limit',price:Number(row.dataset.orderPrice)});});
$('#flowContent').addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)&&e.target.matches('[data-order-price]')){e.preventDefault();e.target.click();}});
$('#floatingMenu').addEventListener('click',e=>{const b=e.target.closest('[data-context-action]');if(!b||!contextPoint)return;const action=b.dataset.contextAction,point=contextPoint;$('#floatingMenu').hidden=true;if(action==='line'){const drawings=chart.getDrawings();drawings.push({type:'horizontal',a:{time:point.time,price:point.price}});chart.setDrawings(drawings);if(!replayTimer){savedDrawings[currentSymbol]=chart.getDrawings();save('drawings-v3',savedDrawings);}}if(action==='alert')openAlert(point.price);if(action==='buy'||action==='sell')openOrder(action,{type:'limit',price:Number(point.price.toFixed(4))});});
const resizer=$('#panelResizer');let resizeStart=null;
resizer.addEventListener('pointerdown',e=>{resizeStart={y:e.clientY,height:$('.analysis-panel').getBoundingClientRect().height};resizer.setPointerCapture(e.pointerId);document.body.classList.add('is-resizing');});
resizer.addEventListener('pointermove',e=>{if(!resizeStart)return;workspace.panelHeight=Math.max(Number(resizer.getAttribute('aria-valuemin')),Math.min(Number(resizer.getAttribute('aria-valuemax')),resizeStart.height+resizeStart.y-e.clientY));fitWorkspace();});
for(const event of ['pointerup','pointercancel'])resizer.addEventListener(event,()=>{if(!resizeStart)return;resizeStart=null;document.body.classList.remove('is-resizing');applyWorkspace();});
resizer.addEventListener('keydown',e=>{if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();workspace.panelHeight=Math.max(Number(resizer.getAttribute('aria-valuemin')),Math.min(Number(resizer.getAttribute('aria-valuemax')),Number(resizer.getAttribute('aria-valuenow'))+(e.key==='ArrowUp'?20:-20)));applyWorkspace();}});
$('#replayScrub').addEventListener('input',e=>{replayTime=replayFrom+(replayTo-replayFrom)*Number(e.target.value)/1000;paintReplay();});
$('#replaySpeed').addEventListener('change',e=>{replaySpeed=Number(e.target.value);});
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openSearch();return;}
  if(e.key==='Escape'){if(!$('#modalBackdrop').hidden){closeModal();return;}$('#floatingMenu').hidden=true;if(workspace.mode==='focus')toggleFocus();setTool('crosshair',false);return;}
  if(!$('#modalBackdrop').hidden){if(e.key==='Tab'){const list=$$('button,input,select,textarea,[tabindex="0"]',$('#modal')).filter(el=>!el.disabled&&el.getClientRects().length),first=list[0],last=list.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}return;}
  if(e.ctrlKey||e.metaKey||e.altKey||/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))return;const k=e.key.toLowerCase();if(k==='s'){e.preventDefault();openSessions();return;}if(navigation.screen!=='terminal')return;if(k==='c')setTool('crosshair');if(k==='t')setTool('trend');if(k==='h')setTool('horizontal');if(k==='a')openAlert();if(k==='f'){e.preventDefault();toggleFocus();}
});
account.schemaVersion=3;persistAccount();applyWorkspace();renderAnalysisToolbar();refreshSessionCatalog();navigation.start();
collectorAuto.start();
if(!collectorAuto.getSnapshot().active){loadChart();feed.start(currentSymbol);}
else{renderChartIdentity();updateLegend();$('#chartMessage').hidden=false;$('#chartMessage').textContent='Conectando ao coletor';}
renderTicker();renderWatchlist();renderDetails();renderAccount();renderFlow();renderAnalysis();renderStatus();renderStorageStatus();updateAlertBadge();
setInterval(()=>{if(document.hidden)return;if(chartDirty&&!replayTimer){paintChart();chartDirty=false;}if(renderDirty){renderTicker();renderWatchlist();renderDetails();if(!replayTimer||replayPaused){renderFlow();renderAnalysis();}updateRiskMarkers();if(activeModal==='order')refreshOrderForm?.();renderDirty=false;}renderStatus();renderStorageStatus();},350);
setInterval(()=>{if(!document.hidden&&!replayTimer&&!collectorAuto.getSnapshot().active)loadChart(true);},60000);
setInterval(()=>{if(!document.hidden){refreshRithmicHistory();renderDirty=true;if(analysisTab==='health')renderHealth();updateMicrostructure();}},1000);
setInterval(checkpointSession,10000);
document.addEventListener('visibilitychange',()=>{if(document.hidden)checkpointSession();});
window.addEventListener('pagehide',()=>{checkpointSession();});
window.addEventListener('beforeunload',()=>{feed.stop();historyAbort?.abort();});
