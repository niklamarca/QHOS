import {analyzeMicrostructure,runEventStudy,runExperiments,parseMBOArchive,queryQueue,MBO_ARCHIVE_TEMPLATE} from 'qhos-module/microstructure-engine.js';
import {computeHeatmap,FlowCanvas} from 'qhos-module/flow-visuals.js';
import {displayTime} from 'qhos-module/market.js';

const finite=value=>typeof value==='number'&&Number.isFinite(value);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(value,digits=2)=>finite(value)?value.toLocaleString('pt-BR',{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—';
const qty=value=>finite(value)?value.toLocaleString('pt-BR',{maximumFractionDigits:8}):'—';
const dateTime=value=>displayTime.dateTime(value,{milliseconds:true});
const expiryText=value=>/^\d{4}-\d{2}-\d{2}$/.test(value??'')?value.split('-').reverse().join('/'):value;
// Only the displayed clock is reduced to milliseconds. Original nanosecond strings stay untouched.
const nsTime=value=>{try{if(typeof value!=='string'||!/^\d+$/.test(value))return '—';const ms=BigInt(value)/1000000n;return ms<=8640000000000000n?dateTime(Number(ms)):'—';}catch{return '—';}};
const money=value=>finite(value)?new Intl.NumberFormat('pt-BR',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(value):'—';
const signed=value=>finite(value)?`${value>=0?'+':''}${num(value,2)}`:'—';
const metric=(label,value,detail='')=>`<div class="micro-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(detail)}</small></div>`;
const empty=(title,text)=>`<div class="micro-empty"><strong>${esc(title)}</strong><p>${esc(text)}</p></div>`;
const notes=value=>Array.isArray(value)?value.map(x=>typeof x==='string'?x:JSON.stringify(x)).join(' · '):String(value??'');
const priceDigits=step=>Math.min(10,Math.max(2,Math.ceil(-Math.log10(step||.01))));
const defaultStep=context=>({BTCUSD:1,ETHUSD:.1,SOLUSD:.01})[context?.symbol??context]??(finite(context?.instrument?.tickSize)&&context.instrument.tickSize>0?context.instrument.tickSize:.01);
const providerOf=context=>context?.instrument?.provider??(['BTCUSD','ETHUSD','SOLUSD'].includes(context?.symbol)?'coinbase':'unknown');
// QHOS V2 plan, rule 18 (25/10 amendment): for Rithmic ES, studies that measure what price did after a condition stay
// off until 21/11, the end of the hypothesis test window. Live, descriptive panels are unaffected.
const ES_LIBERADO_EM=Date.UTC(2026,10,21,3);
const AVISO_ES='Desligado para o ES até 21/11 (plano da V2, regra 18).';
const esBloqueado=context=>Date.now()<ES_LIBERADO_EM&&providerOf(context)==='rithmic'&&/^ES[FGHJKMNQUVXZ]\d{1,2}$/.test(String(context?.instrument?.symbol??String(context?.symbol??'').split(':').pop()));
// QHOS V2 task 9: one dot per layer, exactly as the collector reports it. A report older than PERCURSO_VELHO_MS is
// shown as unknown (gray): the panel never keeps a layer green on its own.
const PERCURSO_VELHO_MS=5000;
const PERCURSO_CAMADAS={rithmic:'Rithmic',sessao:'Sessão',gravacao:'Gravação',livro:'Livro',leitura:'Leitura',coletor:'Coletor'};
const PERCURSO_ESTADOS={ok:['OK','#79c27a'],atencao:['Atenção','#e6b969'],falha:['Falha','#e2675f'],desconhecido:['Desconhecido','#607080']};
const symbolLabel=context=>context?.instrument?.symbol??context?.symbol??'—';
const currencyOf=context=>context?.instrument?.currency??'USD';
const sourceName=context=>context?.sourceLabel||(providerOf(context)==='coinbase'?'Coinbase Exchange':providerOf(context)==='rithmic'?`Rithmic${context?.environment?` · ${context.environment}`:''}`:'Fonte não informada');
const timestamp=value=>finite(value)?value<1e11?value*1000:value:NaN;
const indicatorName=value=>({sweep:'Varredura de preços',absorption:'Absorção candidata',vacuum:'Redução de liquidez'})[value]??value??'Padrão';
const statusText=value=>({candidate:'Candidato · conferir contexto',insufficient:'Observações insuficientes','not-detected':'Não observado'})[value]??'Observação';
const MICRO_VIEWS={
  state:{title:'Estado microestrutural',description:'Acompanhe a condição dos dados, as métricas da janela e os padrões observados no mercado.'},
  pipeline:{title:'Percurso dos dados',description:'Inspecione cada etapa entre a fonte observada, a captura e os produtos de análise.'},
  quotes:{title:'Cotações amostradas',description:'Consulte bid, ask, spread e a cobertura das cotações recebidas por ativo.'},
  experiment:{title:'Carteira experimental',description:'Compare tendência, reversão e referência com a mesma fonte, capital e custos definidos por você.'},
  queue:{title:'Consulta de fila',description:'Localize uma ordem pelo ID e examine sua posição no estado final de um arquivo MBO.'},
  liquidity:{title:'Dinâmica de liquidez',description:'Compare snapshots do livro e explore como a liquidez observada mudou ao longo do tempo.'},
  mbo:{title:'Lifecycle MBO',description:'Inspecione a sequência de eventos de ordens individuais preservada no arquivo importado.'},
  events:{title:'Estudo de eventos',description:'Meça o movimento do mid-price antes e depois de negócios com uma condição e janela definidas.'},
  integrity:{title:'Integridade e linhagem',description:'Confira a origem, a cobertura, as lacunas e a persistência dos dados usados neste contexto.'}
};


/** A browser-local research workspace. It never sends orders or invents MBO. */
export class MicrostructurePanel {
  constructor(container,callbacks={}) {
    if(!container)throw new TypeError('MicrostructurePanel requires a container');
    this.container=container;this.callbacks=callbacks;this.context={};this.lastRender=0;this.timer=null;
    this.heatmap=null;this.heatVisible=false;this.archive=null;this.catalog=[];this.quoteSamples=new Map();
    this.quoteSeen=new Map();this.quoteRecordedAt=new Map();this.pipelineNode='source';this.lastSymbol=null;
    this.studyRun=0;this.mboRun=0;this.destroyed=false;this.activeView=null;this.viewScroll=new Map();
    container.classList.add('micro-panel');
    container.innerHTML=this.markup();
    this.$=selector=>container.querySelector(selector);
    this.bind();
    if(typeof IntersectionObserver!=='undefined'){
      this.heatObserver=new IntersectionObserver(entries=>{this.heatVisible=this.activeView==='liquidity'&&entries.some(entry=>entry.isIntersecting);if(this.heatVisible)this.renderHeatmap();},{root:container,rootMargin:'80px'});
      this.heatObserver.observe(this.$('#microHeatmap'));
    }
    this.showView('state');
    this.refreshSessions();
  }

  markup(){return `
    <header class="micro-heading"><div class="micro-heading-copy"><span class="micro-eyebrow">PESQUISA E OBSERVAÇÃO</span><h2 id="microScreenTitle" tabindex="-1">Estado microestrutural</h2><p id="microScreenDescription"></p></div><div class="micro-context"><span class="micro-chip" id="microMode">Aguardando captura</span><strong id="microSymbol">—</strong><small id="microClock">Horários em ${displayTime.label()}</small></div></header>
    <div class="micro-content">
      <div class="micro-card micro-mbo-source" data-micro-shared="mbo" aria-labelledby="microMBOSourceTitle" hidden><div class="micro-card-heading"><div><h4 id="microMBOSourceTitle">Arquivo de ordens individuais</h4><small>Este painel usa somente arquivos MBO importados. Importe um arquivo compatível para explorar eventos de ordens individuais e consultar a fila.</small></div><div class="micro-inline-controls"><button class="micro-button" type="button" id="microMBOTemplate">Baixar esquema vazio</button><label class="micro-upload">Importar JSON MBO<input id="microMBOFile" type="file" accept=".json,application/json" aria-label="Importar arquivo MBO em JSON"></label></div></div><div id="microMBOStatus" class="micro-status-line">Nenhum arquivo MBO carregado. Não há conexão MBO ou fila de bolsa ativa.</div><div id="microMBOSummary" class="micro-small-metrics"></div><details class="micro-method"><summary>Formato do arquivo e hipótese de fila</summary><p class="micro-note">O botão baixa um template vazio, sem negociações de exemplo. Preencha somente com dados cuja origem você possa identificar. O limite é 10 MB, com até 20.000 ordens ativas e 50.000 eventos.</p><dl class="micro-facts"><div><dt>Snapshot</dt><dd>sequence e timeNs: strings de inteiros. orders: lista de id, side (buy/sell), priceTicks (string), size (inteiro positivo) e priority (ordinal em string).</dd></div><div><dt>Eventos</dt><dd>sequence, timeNs, action e orderId. Sequências consecutivas, horários monotônicos. add exige side, priceTicks e size; modify exige size e aceita priceTicks; execute reduz size; cancel remove a ordem inteira.</dd></div><div><dt>Preço e tamanho</dt><dd>Preço = priceTicks × tickSize. Quantidades são unidades inteiras do arquivo; o tamanho de cada unidade precisa ser definido na fonte.</dd></div><div><dt>Prioridade FIFO</dt><dd>priority do snapshot não pode superar sua sequence. Aumento de tamanho ou troca de preço perde prioridade; redução conserva. Essa regra é uma hipótese do arquivo, sem validar o matching de uma bolsa.</dd></div></dl></details><p class="micro-error" id="microMBOError" role="alert" hidden></p></div>
      <section class="micro-section" id="micro-state" data-micro-view="state" aria-label="Estado microestrutural" hidden><div class="micro-status-line" id="microOverviewStatus" role="status">Aguardando dados observados.</div><div class="micro-metrics" id="microMetrics"></div><article class="micro-card"><div class="micro-card-heading"><h4>Leitura da janela</h4><span>60 segundos</span></div><div id="microIndicators"></div><p class="micro-note">Padrões são candidatos de pesquisa. L2 agregado não identifica participantes, intenção ou ordens ocultas.</p></article></section>
      <section class="micro-section" id="micro-pipeline" data-micro-view="pipeline" aria-label="Percurso dos dados" hidden><article class="micro-card" id="microRithmicPath" aria-label="Percurso Rithmic informado pelo coletor" hidden></article><div class="micro-pipeline" aria-label="Etapas do percurso dos dados"><button type="button" data-micro-node="source" class="active" aria-pressed="true"><b>01</b><strong>Fonte</strong><small id="microPipelineSource">Fonte / arquivo</small></button><button type="button" data-micro-node="capture" aria-pressed="false"><b>02</b><strong>Captura</strong><small>Trades + snapshots</small></button><button type="button" data-micro-node="normalization" aria-pressed="false"><b>03</b><strong>Normalização</strong><small>Lado, preço e tempo</small></button><button type="button" data-micro-node="storage" aria-pressed="false"><b>04</b><strong>Gravação</strong><small>IndexedDB local</small></button><button type="button" data-micro-node="observation" aria-pressed="false"><b>05</b><strong>Observação</strong><small>Janelas e cobertura</small></button><button type="button" data-micro-node="products" aria-pressed="false"><b>06</b><strong>Produtos</strong><small>Gráficos e estudos</small></button></div><article class="micro-card micro-node-detail" id="microNodeDetail" aria-live="polite"></article></section>
      <section class="micro-section" id="micro-quotes" data-micro-view="quotes" aria-label="Cotações amostradas" hidden><article class="micro-card"><div class="micro-card-heading"><div><h4 id="microQuotesTitle">Cotações amostradas</h4><small id="microQuoteNote">Até uma amostra por segundo por ativo, somente quando a cotação muda.</small></div><span class="micro-chip muted" id="microQuoteMode">Captura ao vivo</span></div><div class="micro-table-wrap" id="microQuotes"></div></article></section>
      <section class="micro-section" id="micro-experiment" data-micro-view="experiment" aria-label="Carteira experimental" hidden><article class="micro-card"><div class="micro-card-heading"><div><h4>Experimento de estratégias</h4><small>Tendência, reversão e referência sobre candles fechados. Mesma fonte e mesmos custos.</small></div><span class="micro-chip muted" id="microExperimentAvailability">Aguardando histórico</span></div><form class="micro-form micro-experiment-form" id="microExperimentForm"><label>Capital inicial · USD<input id="microCapital" type="number" min="1" max="1000000000" step="any" value="100000" required></label><label>Quantidade · ativo-base<input id="microQuantity" type="number" min="0.00000001" max="1000000" step="any" value="0.01" required></label><label>Taxa · bps<input id="microFee" type="number" min="0" max="1000" step="0.1" value="10" required></label><label>Slippage · ticks<input id="microSlippage" type="number" min="0" max="1000" step="1" value="1" required></label><label>Tick · USD<input id="microTick" type="number" min="0.00000001" max="1000000" step="any" value="0.01" required></label><button class="micro-button primary" id="microRunExperiments" type="submit" disabled>Rodar experimento</button></form><p class="micro-note" id="microExperimentSource">São necessários pelo menos 12 candles fechados e um período conhecido.</p><p class="micro-error" id="microExperimentError" role="alert" hidden></p><div id="microExperimentResults">${empty('Uma comparação controlada','Defina os custos e rode sobre o histórico disponível. O resultado não cria operações na sua conta virtual.')}</div></article></section>
      <section class="micro-section" id="micro-queue" data-micro-view="queue" aria-label="Consulta de fila" hidden><article class="micro-card"><div class="micro-card-heading"><div><h4>Posição na fila por ID da ordem</h4><small>Consulta do estado final reconstruído do arquivo importado.</small></div><span class="micro-chip muted">Hipótese FIFO</span></div><form class="micro-form micro-queue-form" id="microQueueForm"><label>ID exato da ordem<input id="microOrderId" autocomplete="off" maxlength="160" placeholder="order_id do arquivo" required disabled></label><button class="micro-button primary" id="microQueueSubmit" type="submit" disabled>Consultar fila</button></form><p class="micro-note" id="microQueueReason">Consulta disponível após importar MBO válido. Prioridade FIFO é uma hipótese explícita do arquivo, sem inferir prioridade de outra bolsa.</p><div id="microQueueResult"></div></article></section>
      <section class="micro-section" id="micro-liquidity" data-micro-view="liquidity" aria-label="Dinâmica de liquidez" hidden><div class="micro-two-col"><article class="micro-card"><div class="micro-card-heading"><h4>Variação entre snapshots</h4><span>L2 agregado</span></div><div class="micro-small-metrics" id="microLiquidity"></div><p class="micro-note" id="microLiquidityNote">Uma redução no livro pode decorrer de execução ou cancelamento; não é classificada como trade.</p></article><article class="micro-card"><div class="micro-card-heading"><h4>Volume executado por preço</h4><span>Últimos 60 s</span></div><div class="micro-table-wrap" id="microFootprint"></div><p class="micro-note">Até 12 faixas de maior volume. Compra = agressão no ask; venda = agressão no bid.</p></article></div><div class="micro-card"><div class="micro-card-heading"><div><h4>Histórico de liquidez L2</h4><small>Bid em verde · ask em âmbar · intensidade = quantidade observada</small></div><div class="micro-inline-controls"><label>Janela<select id="microHeatWindow" aria-label="Janela do heatmap de pesquisa"><option value="60">1 minuto</option><option value="180" selected>3 minutos</option><option value="600">10 minutos</option></select></label><label><span id="microPriceStepLabel">Preço / faixa</span><input id="microPriceStep" type="number" min="0.00000001" step="any" value="1" aria-label="Faixa de preço da microestrutura"></label></div></div><div class="micro-heatmap" id="microHeatmap"></div><p class="micro-note" id="microHeatNote">Até 20 níveis por lado, em snapshots amostrados. Espaços sem registros ficam vazios.</p></div></section>
      <section class="micro-section" id="micro-mbo" data-micro-view="mbo" aria-label="Lifecycle MBO" hidden><article class="micro-card"><div class="micro-card-heading"><h4>Ciclo de vida das ordens</h4><span>Sequência original do arquivo</span></div><div class="micro-table-wrap micro-lifecycle-table" id="microLifecycle">${empty('Sem eventos individuais','Importe snapshot e eventos de um arquivo MBO. O painel não converte atualizações L2 em ordens fictícias.')}</div></article></section>
      <section class="micro-section" id="micro-events" data-micro-view="events" aria-label="Estudo de eventos" hidden><article class="micro-card"><div class="micro-card-heading"><div><h4>Antes e depois do negócio</h4><small>Mid-price observado antes e depois de cada negócio. Sem preencher snapshots ausentes.</small></div><div class="micro-inline-controls"><button class="micro-button" type="button" data-micro-action="sessions">Abrir sessões ↗</button><button class="micro-button" id="microRefreshSessions" type="button">Atualizar sessões</button></div></div><form class="micro-form micro-event-form" id="microEventStudyForm"><label>Fonte<select id="microStudySource"><option value="current">Captura atual</option></select></label><label>Condição<select id="microStudySide"><option value="all">Todos os negócios</option><option value="buy">Compra no ask</option><option value="sell">Venda no bid</option></select></label><label>Quantidade mínima<input id="microStudyMin" type="number" min="0" max="1000000" step="any" value="0" required></label><label>Janela<select id="microStudyWindow"><option value="1000">1 segundo</option><option value="5000">5 segundos</option></select></label><button class="micro-button primary" type="submit" id="microRunStudy">Analisar eventos</button></form><p class="micro-error" id="microStudyError" role="alert" hidden></p><div id="microStudyResults">${empty('Qual foi o percurso do preço?','Escolha a captura atual ou uma sessão salva. O retorno é uma observação posterior ao evento, calculada apenas quando os snapshots necessários existem.')}</div></article></section>
      <section class="micro-section" id="micro-integrity" data-micro-view="integrity" aria-label="Integridade e linhagem" hidden><article class="micro-card"><div class="micro-card-heading"><h4>Origem e cobertura da captura</h4><span>Inspecionável</span></div><dl class="micro-facts" id="microIntegrity"></dl></article></section>
      <footer class="micro-footer">Pesquisa local · dados observados · custos hipotéticos · sem envio de ordens à bolsa</footer>
    </div>`;}

  showView(key,{focus=false}={}){
    if(this.destroyed)return;
    const next=Object.prototype.hasOwnProperty.call(MICRO_VIEWS,key)?key:'state',view=MICRO_VIEWS[next],changed=this.activeView!==next;
    if(changed&&this.activeView)this.viewScroll.set(this.activeView,this.container.scrollTop);
    this.activeView=next;this.container.dataset.activeMicroView=next;
    for(const section of this.container.querySelectorAll('[data-micro-view]'))section.hidden=section.dataset.microView!==next;
    this.$('[data-micro-shared="mbo"]').hidden=!['queue','mbo'].includes(next);
    this.$('#microScreenTitle').textContent=view.title;this.$('#microScreenDescription').textContent=view.description;
    this.container.setAttribute('aria-labelledby','microScreenTitle');
    if(changed)this.container.scrollTop=this.viewScroll.get(next)??0;
    this.heatVisible=next==='liquidity';if(this.heatVisible)this.renderHeatmap();
    if(focus)this.$('#microScreenTitle').focus({preventScroll:true});
    return next;
  }

  bind(){
    this.container.addEventListener('click',event=>{
      const action=event.target.closest('[data-micro-action]')?.dataset.microAction;
      if(action==='chart')this.callbacks.openChart?.();
      if(action==='sessions')this.callbacks.openSessions?.();
      if(action==='footprint')this.callbacks.openAnalysis?.('footprint');
      const execution=event.target.closest('[data-micro-execution]');
      if(execution){const focused=this.callbacks.focusExecution?.(execution.dataset.microExecution);if(!focused)this.callbacks.notify?.('A execução não está no gráfico atual. Abra a sessão e o ativo correspondentes.','error');}
      const node=event.target.closest('[data-micro-node]');
      if(node){this.pipelineNode=node.dataset.microNode;for(const b of this.container.querySelectorAll('[data-micro-node]')){b.classList.toggle('active',b===node);b.setAttribute('aria-pressed',String(b===node));}this.renderPipeline();}
    });
    this.$('#microHeatWindow').addEventListener('change',()=>this.renderHeatmap());
    this.$('#microPriceStep').addEventListener('change',()=>{const value=Number(this.$('#microPriceStep').value);if(!finite(value)||value<=0||value>1e8){this.$('#microPriceStep').value=String(defaultStep(this.context));this.callbacks.notify?.('Informe uma faixa de preço positiva e finita.','error');}this.renderHeatmap();this.renderFootprint();});
    this.$('#microMBOFile').addEventListener('change',event=>this.importMBO(event.target.files?.[0]));
    this.$('#microMBOTemplate').addEventListener('click',()=>this.downloadTemplate());
    this.$('#microQueueForm').addEventListener('submit',event=>{event.preventDefault();this.inspectQueue();});
    this.$('#microExperimentForm').addEventListener('submit',event=>{event.preventDefault();this.runExperiment();});
    this.$('#microEventStudyForm').addEventListener('submit',event=>{event.preventDefault();this.runStudy();});
    this.$('#microRefreshSessions').addEventListener('click',()=>this.refreshSessions());
  }

  update(context){
    if(this.destroyed)return;
    const previous=this.context,next=context??{},key=value=>`${value.symbol??''}:${providerOf(value)}:${value.environment??''}:${!!value.replaying}:${!!value.imported}:${value.captureCreatedAt??''}:${value.generation??value.feedStatus?.generation??''}`;
    if(previous.symbol&&(key(previous)!==key(next)||(finite(previous.until)&&finite(next.until)&&next.until<previous.until))){
      this.studyRun++;this.$('#microRunStudy').disabled=false;this.lastStudyResult=null;this.lastExperimentResult=null;
      this.$('#microExperimentResults').innerHTML=empty('Contexto alterado','Rode o experimento novamente para usar o ativo e o cursor atuais.');
      this.$('#microStudyResults').innerHTML=empty('Contexto alterado','Analise novamente a captura ou a sessão escolhida. O resultado anterior não acompanha este cursor.');
      this.error('#microExperimentError',null);this.error('#microStudyError',null);
      this.quoteSamples.clear();this.quoteSeen.clear();this.quoteRecordedAt.clear();
    }
    this.context=next;this.sampleQuotes();
    if(this.lastSymbol!==key(this.context)){this.lastSymbol=key(this.context);this.$('#microPriceStep').value=String(defaultStep(this.context));this.$('#microTick').value=String(this.context.instrument?.tickSize??.01);}
    const remaining=1000-(Date.now()-this.lastRender);
    if(remaining<=0){clearTimeout(this.timer);this.timer=null;this.render();}
    else if(!this.timer)this.timer=setTimeout(()=>{this.timer=null;this.render();},remaining);
  }
  // Refresh labels from retained results; never rerun a study, resample quotes or rebuild heatmap buckets.
  refreshTimeZone(){
    if(this.destroyed)return;
    this.$('#microClock').textContent=finite(this.displayEnd)?dateTime(this.displayEnd):`Horários em ${displayTime.label()}`;
    this.renderOverview();this.renderPipeline();this.renderQuotes();this.renderSessionOptions();
    this.heatmap?.refreshTimeZone();
    if(this.lastStudyResult)this.renderStudyResults(...this.lastStudyResult);
    if(this.lastExperimentResult)this.renderExperimentResults(...this.lastExperimentResult);
    if(this.archive)this.renderMBOEvents();
    if(this.queueResult)this.renderQueueResult(this.queueResult);
  }
  cutoff(){return finite(this.context.until)?this.context.until:Date.now();}
  sourceLabel(context=this.context){const source=sourceName(context);return context.imported?`Arquivo importado · origem não verificada · ${source}`:context.replaying?`Replay · ${source}`:`${source} · ${context.feedStatus?.state==='live'?'ao vivo':'sem confirmação ao vivo'}`;}
  contextBooks(){const c=this.context,list=Array.isArray(c.books)?c.books:[];if(!c.replaying&&c.liveBook?.ready&&finite(c.liveBook.time)&&(!list.length||c.liveBook.time>list.at(-1).time))return [...list,c.liveBook];return list;}
  error(id,error){const el=this.$(id);el.hidden=!error;el.textContent=error?String(error.message??error):'';}
  render(){
    if(this.destroyed)return;this.lastRender=Date.now();const c=this.context,end=this.cutoff();this.displayEnd=end;
    this.summary=analyzeMicrostructure({trades:c.trades??[],books:this.contextBooks(),until:end,windowMs:60000});
    this.$('#microMode').textContent=c.imported?'IMPORTADO · NÃO VERIFICADO':c.replaying?'REPLAY':`${sourceName(c)} · OBSERVADO`;
    this.$('#microMode').classList.toggle('warning',!!c.replaying||!!c.imported||c.feedStatus?.state!=='live');this.$('#microSymbol').textContent=symbolLabel(c);this.$('#microClock').textContent=dateTime(end);
    this.$('#microPriceStepLabel').textContent=`${currencyOf(c)} / faixa`;
    this.renderOverview();this.renderLiquidity();this.renderFootprint();this.renderPipeline();this.renderQuotes();this.renderExperimentAvailability();
    if(this.heatVisible)this.renderHeatmap();
  }
  renderOverview(){
    const c=this.context,s=this.summary??{},unit=c.instrument?.quantityUnit??(c.symbol??'').replace(/USD$/,''),currency=currencyOf(c),digits=c.instrument?.priceDecimals??2;
    const state=c.replaying?(c.replayPaused?'Replay pausado no cursor indicado':'Replay em reprodução'):c.feedStatus?.state==='live'?'Feed ao vivo':c.feedStatus?.state==='connecting'?'Conectando à fonte':'Feed sem confirmação ao vivo';
    this.$('#microOverviewStatus').textContent=`${this.sourceLabel()} · ${state}. ${s.bookFresh?'Livro recente no contexto observado.':'Livro ausente ou sem atualização recente.'}${c.feedStatus?.unclassifiedTrades?` ${qty(c.feedStatus.unclassifiedTrades)} negócios sem agressor informado ficaram fora das análises direcionais.`:''}`;
    this.$('#microMetrics').innerHTML=[metric('Melhor bid / ask',`${num(s.bestBid,digits)} / ${num(s.bestAsk,digits)}`,`${currency} · livro observado`),metric('Spread',`${num(s.spread,digits)} ${currency}`,`${num(s.spreadBps)} bps`),metric('Desequilíbrio L2',finite(s.imbalance)?`${num(s.imbalance*100,1)}%`:'—','(bid − ask) / (bid + ask)'),metric('Delta · 60 s',`${s.delta>=0?'+':''}${qty(s.delta)}`,`${qty(s.tradeCount)} negócios · ${unit}`),metric('VWAP · 60 s',num(s.vwap,digits),`${currency} · ponderado pelo volume`),metric('Idade do livro',finite(s.bookAgeMs)?`${num(s.bookAgeMs/1000,1)} s`:'—',c.replaying?'Em relação ao cursor':'Em relação ao navegador')].join('');
    this.$('#microIndicators').innerHTML=Array.isArray(s.indicators)&&s.indicators.length?s.indicators.map(item=>`<div class="micro-observation"><span class="micro-state ${item.status==='candidate'?'candidate':''}"></span><div><strong>${esc(indicatorName(item.kind))}</strong><small>${esc(statusText(item.status))}</small><p>${esc(item.description)}</p></div></div>`).join(''):empty('Aguardando uma janela observável','Os candidatos aparecem quando há negócios e snapshots suficientes.');
    const recording=c.recording??{},gaps=(c.gaps??[]).filter(gap=>!finite(gap.from)||gap.from<=this.cutoff()),rows=[['Fonte',this.sourceLabel()],['Captura iniciada',dateTime(c.captureCreatedAt)],['Negócios retidos',`${qty(c.trades?.length??0)} / 20.000`],['Snapshots retidos',`${qty(c.books?.length??0)} / 600`],['Janela truncada',recording.tradeTruncated||recording.bookTruncated||recording.truncated?'Sim · cobertura parcial':'Sem truncamento informado'],['Lacunas registradas',String(gaps.length)],['IDs ainda não recuperados',qty(c.feedStatus?.unrecovered??0)],['Último checkpoint local',c.lastSavedAt?dateTime(c.lastSavedAt):'Nenhum checkpoint confirmado'],['Armazenamento',c.storageError||`${c.sessionCount??0} sessões locais · autosave ${c.autoSave?'ativo':'desativado'}`]];
    rows.splice(1,0,['Instrumento',`${symbolLabel(c)} · ${c.instrument?.exchange??'—'}${c.instrument?.expiry?` · vencimento ${expiryText(c.instrument.expiry)}`:''}`],['Ambiente',c.environment||'Não informado'],['Preço / quantidade',`${currency} / ${unit}`],['Agressor não informado',qty(c.feedStatus?.unclassifiedTrades??0)],['Tempo','Gráfico em milissegundos; timestamp original em ns preservado quando fornecido.'],['MBO / fila','Somente arquivo importado; sem ligação MBO ao vivo.']);
    this.$('#microIntegrity').innerHTML=rows.map(([key,value])=>`<div><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`).join('');
  }
  renderHeatmap(){
    if(!this.heatVisible||!this.context.symbol)return;const c=this.context,windowMs=Number(this.$('#microHeatWindow').value)*1000,priceStep=Number(this.$('#microPriceStep').value);
    if(!finite(priceStep)||priceStep<=0)return;
    if(!this.heatmap)this.heatmap=new FlowCanvas(this.$('#microHeatmap'),{type:'heatmap',formatPrice:value=>num(value,priceDigits(Number(this.$('#microPriceStep').value))),formatQuantity:qty});
    const result=computeHeatmap(c.books??[],{until:this.cutoff(),windowSeconds:windowMs/1000,step:priceStep,maxRows:48,maxColumns:180});
    this.heatmap.setData(result);this.$('#microHeatNote').textContent=`${this.sourceLabel()} · ${(c.books??[]).length} snapshots retidos · até 20 níveis por lado, no máximo 1/s. Colunas agrupadas mostram média observada; lacunas permanecem vazias.`;
  }
  renderLiquidity(){
    const d=this.summary?.liquidityDynamics??{};
    this.$('#microLiquidity').innerHTML=d.available?[metric('Bid adicionado',qty(d.bidAdded)),metric('Bid reduzido',qty(d.bidRemoved)),metric('Ask adicionado',qty(d.askAdded)),metric('Ask reduzido',qty(d.askRemoved))].join(''):empty('Snapshots insuficientes',d.reason??'Aguarde duas observações compatíveis do livro.');
    this.$('#microLiquidityNote').textContent=d.available?`${qty(d.changedLevels)} níveis alterados em ${num(d.elapsedMs/1000,1)} s, somente nos preços presentes nos dois snapshots. Redução não comprova execução ou cancelamento.`:String(d.reason??'Não é possível reconstruir ordens individuais a partir destas quantidades agregadas.');
  }
  renderFootprint(){
    const end=this.cutoff(),step=Number(this.$('#microPriceStep').value),map=new Map();if(!finite(step)||step<=0)return;
    for(const trade of this.context.trades??[]){if(!finite(trade.time)||trade.time>end||trade.time<end-60000||!finite(trade.price)||!finite(trade.size)||trade.price<=0||trade.size<=0||!['buy','sell'].includes(trade.side))continue;const index=Math.floor(trade.price/step+1e-9);let row=map.get(index);if(!row){row={price:index*step,buy:0,sell:0,count:0};map.set(index,row);}row[trade.side]+=trade.size;row.count++;}
    const rows=[...map.values()].sort((a,b)=>(b.buy+b.sell)-(a.buy+a.sell)).slice(0,12).sort((a,b)=>b.price-a.price);
    this.$('#microFootprint').innerHTML=rows.length?`<table><thead><tr><th>Preço · ${esc(currencyOf(this.context))}</th><th>Venda / bid</th><th>Compra / ask</th><th>Delta</th></tr></thead><tbody>${rows.map(row=>`<tr><th>${num(row.price,priceDigits(step))}</th><td class="micro-negative">${qty(row.sell)}</td><td class="micro-positive">${qty(row.buy)}</td><td>${row.buy-row.sell>=0?'+':''}${qty(row.buy-row.sell)}</td></tr>`).join('')}</tbody></table>`:empty('Sem negócios nesta janela','A captura registra somente as execuções observadas.');
  }
  renderPipeline(){
    const c=this.context,s=this.summary??{},from=c.trades?.[0]?.time,to=c.trades?.at(-1)?.time,coinbase=providerOf(c)==='coinbase';
    this.$('#microPipelineSource').textContent=sourceName(c);
    const sourceDescription=c.imported?'Dados de um arquivo importado. Origem, permissões e completude não são autenticadas.':coinbase?'Candles REST e negócios / livro WebSocket públicos da Coinbase. A fonte corresponde a esta bolsa.':`Dados entregues pelo serviço QHOS para o contrato confirmado. Ambiente: ${c.environment||'não informado'}. Conectividade e disponibilidade são indicadas no status; MBO permanece disponível apenas por arquivo.`;
    const normalization=coinbase?'O lado maker da Coinbase é invertido para expressar agressão. Trades usam horário de bolsa normalizado em ms; snapshots usam chegada local. IDs duplicados são removidos.':'O adaptador informa o agressor. Negócios sem esse lado ficam fora das análises direcionais. O gráfico usa ms; timestamps originais em ns são preservados quando fornecidos, sem convertê-los em Number.';
    const nodes={
      source:['Fonte',this.sourceLabel(),sourceDescription],
      capture:['Captura',`${qty(c.trades?.length??0)} trades · ${qty(c.books?.length??0)} snapshots`,`Primeiro trade retido: ${dateTime(from)}. Último: ${dateTime(to)}. Livro gravado em até 20 níveis por lado e no máximo um snapshot por segundo.`],
      normalization:['Normalização',`Agressor · ${currencyOf(c)} · milissegundos`,normalization],
      storage:['Gravação',c.storageError?'Falha de armazenamento':c.lastSavedAt?`Salvo ${dateTime(c.lastSavedAt)}`:'Sem checkpoint confirmado',`${c.sessionCount??0} sessões locais. Autosave ${c.autoSave?'ativo':'desativado'}. Persistência em IndexedDB neste navegador; limpeza do armazenamento pode remover os arquivos.`],
      observation:['Observação',`${qty(s.tradeCount??0)} negócios na janela de 60 s`,'As análises respeitam o cursor do replay e a janela retida. Ausência de negócios não prova perda de dados; snapshots ausentes não são interpolados.'],
      products:['Produtos','Gráfico · fluxo · estudos','CVD, footprint, perfil e marcações partem dos negócios com agressor informado. Heatmap usa snapshots L2. Estudos são cálculos locais; não enviam ordens à bolsa. Carteira experimental disponível somente para o modelo spot Coinbase.']
    };
    const [title,value,description]=nodes[this.pipelineNode]??nodes.source;
    this.$('#microNodeDetail').innerHTML=`<span class="micro-eyebrow">${esc(title)}</span><strong>${esc(value)}</strong><p>${esc(description)}</p>`;
    this.renderRithmicPath();
  }
  renderRithmicPath(){
    const c=this.context,box=this.$('#microRithmicPath'),p=c.pipeline;
    box.hidden=providerOf(c)!=='rithmic'||!!c.replaying||!!c.imported;
    if(box.hidden){box.innerHTML='';return;}
    const heading=chip=>`<div class="micro-card-heading"><div><h4>Percurso Rithmic</h4><small>Uma bolinha por camada, como o coletor informa. O coletor só lê o arquivo gravado: não fala com a Rithmic.</small></div>${chip}</div>`;
    if(!p||!Array.isArray(p.stages)){box.innerHTML=heading('<span class="micro-chip muted">Sem percurso</span>')+empty('Percurso não recebido','O coletor não está conectado ou não enviou o percurso. Estado de todas as camadas desconhecido.');return;}
    const age=Date.now()-p.receivedAt,old=!finite(age)||age>PERCURSO_VELHO_MS;
    const chip=old?`<span class="micro-chip warning">Sem atualização há ${finite(age)?Math.round(age/1000):'?'} s · estado desconhecido</span>`:`<span class="micro-chip">Atualizado ${dateTime(p.time)}</span>`;
    box.innerHTML=heading(chip)+p.stages.map(stage=>{
      const [label,color]=PERCURSO_ESTADOS[old?'desconhecido':stage.state]??PERCURSO_ESTADOS.desconhecido;
      const fields=stage.fields.map(field=>`${esc(field.label)}: ${esc(field.value)}`).join(' · ');
      return `<div class="micro-observation" data-percurso="${esc(stage.id)}" data-estado="${old?'desconhecido':esc(stage.state)}"><span class="micro-state" style="background:${color}"></span><div><strong>${esc(PERCURSO_CAMADAS[stage.id]??stage.id)} · ${esc(label)}</strong><small>${old?'Sem atualização recente do coletor':`${esc(stage.reason)} · desde ${dateTime(stage.since)}`}</small>${fields?`<p>${fields}</p>`:''}</div></div>`;
    }).join('');
  }
  sampleQuotes(){
    const c=this.context;if(c.replaying)return;const now=Date.now(),provider=providerOf(c);
    for(const asset of c.assets??[]){
      const assetProvider=asset.provider??(String(asset.symbol).startsWith('rithmic:')?'rithmic':'coinbase');
      if(assetProvider!==provider||typeof asset.symbol!=='string'||!finite(asset.updatedAt)||asset.updatedAt<=0||!finite(asset.bid)||asset.bid<=0||!finite(asset.ask)||asset.ask<=asset.bid||!finite(asset.price)||asset.price<=0)continue;
      if(this.quoteSeen.get(asset.symbol)===asset.updatedAt||now-(this.quoteRecordedAt.get(asset.symbol)??0)<1000)continue;
      this.quoteSeen.set(asset.symbol,asset.updatedAt);this.quoteRecordedAt.set(asset.symbol,now);
      const rows=this.quoteSamples.get(asset.symbol)??[];
      rows.push({time:asset.updatedAt,price:asset.price,bid:asset.bid,ask:asset.ask,spread:asset.ask-asset.bid,displaySymbol:asset.displaySymbol??(asset.symbol===c.symbol?symbolLabel(c):asset.symbol.split(':').at(-1)),currency:asset.currency??currencyOf(c),priceDecimals:asset.priceDecimals??(asset.symbol===c.symbol?c.instrument?.priceDecimals:2)??2});
      if(rows.length>600)rows.shift();this.quoteSamples.set(asset.symbol,rows);
    }
  }
  renderQuotes(){
    const c=this.context,replay=!!c.replaying;
    this.$('#microQuotesTitle').textContent=`Cotações amostradas · ${sourceName(c)}`;
    this.$('#microQuoteMode').textContent=replay?'AMOSTRAGEM PAUSADA NO REPLAY':c.feedStatus?.state==='live'?'CAPTURA AO VIVO':'AGUARDANDO FONTE';
    this.$('#microQuoteNote').textContent=replay?'A amostragem de cotações fica pausada durante o replay. A tabela não representa a sessão reproduzida.':'Até uma amostra por segundo por ativo, quando updatedAt muda; retenção de 600 amostras por ativo. Alterar a fonte ou a captura reinicia esta tabela.';
    const rows=[...this.quoteSamples].map(([symbol,samples])=>({symbol,samples,last:samples.at(-1)}));
    this.$('#microQuotes').innerHTML=rows.length?`<table><thead><tr><th>Ativo</th><th>Amostras</th><th>Último</th><th>Bid / ask</th><th>Spread mín. / máx.</th><th>Horário ${displayTime.label()}</th></tr></thead><tbody>${rows.map(({symbol,samples,last})=>`<tr><th title="${esc(symbol)}">${esc(last.displaySymbol)}</th><td>${samples.length}</td><td>${num(last.price,last.priceDecimals)} ${esc(last.currency)}</td><td>${num(last.bid,last.priceDecimals)} / ${num(last.ask,last.priceDecimals)}</td><td>${num(Math.min(...samples.map(row=>row.spread)),last.priceDecimals)} / ${num(Math.max(...samples.map(row=>row.spread)),last.priceDecimals)}</td><td>${dateTime(last.time)}</td></tr>`).join('')}</tbody></table>`:empty('Aguardando cotações válidas','As amostras começam com preços bid / ask recebidos da fonte, sem séries sintéticas.');
  }
  async importMBO(file){
    if(!file)return;this.error('#microMBOError',null);const run=++this.mboRun,input=this.$('#microMBOFile');input.disabled=true;
    try{
      if(esBloqueado(this.context))throw new Error(AVISO_ES);
      if(file.size>10*1024*1024)throw new Error('O arquivo MBO deve ter no máximo 10 MB.');
      const archive=parseMBOArchive(await file.text());if(this.destroyed||run!==this.mboRun)return;if(Date.now()<ES_LIBERADO_EM&&/^ES/i.test(String(archive.symbol)))throw new Error(AVISO_ES);this.archive=archive;const s=archive.summary??{};
      this.$('#microMBOStatus').textContent=`${file.name} · ${archive.symbol} · ${archive.venue} · origem importada não verificada. Prioridade ${archive.priority??'não informada'}.${s.crossed?' O estado final está cruzado; confira o contexto do arquivo.':''}`;
      this.$('#microMBOSummary').innerHTML=[metric('Ordens abertas',String(s.orderCount??archive.orders?.length??0)),metric('Eventos',String(s.eventCount??archive.events?.length??0)),metric('Sequência final',String(s.sequence??'—')),metric('Bid / ask',`${s.bestBid??'—'} / ${s.bestAsk??'—'}`)].join('');
      this.renderMBOEvents();this.$('#microOrderId').disabled=false;this.$('#microQueueSubmit').disabled=false;
      this.$('#microQueueReason').textContent='Consulta do estado final reconstruído do arquivo. FIFO é uma hipótese explícita; origem e completude não são autenticadas. Quantidades em unidades inteiras do arquivo.';
      this.$('#microQueueResult').innerHTML='';this.queueResult=null;this.callbacks.notify?.('Arquivo MBO validado e carregado para pesquisa.');
    }catch(error){if(!this.destroyed&&run===this.mboRun)this.error('#microMBOError',error);}
    finally{if(!this.destroyed&&run===this.mboRun){input.value='';input.disabled=false;}}
  }
  renderMBOEvents(){
    const events=this.archive?.events??[];
    this.$('#microLifecycle').innerHTML=events.length?`<table><thead><tr><th>Sequência</th><th>Horário ${displayTime.label()} / original · ns</th><th>Evento</th><th>ID da ordem</th><th>Lado</th><th>Preço · ticks</th><th>Quantidade</th></tr></thead><tbody>${events.slice(-80).map(event=>`<tr><td>${esc(event.sequence??'—')}</td><td>${nsTime(event.timeNs)}<br><small>${esc(event.timeNs??'—')} ns</small></td><td>${esc(event.type??event.action??'—')}</td><td class="micro-id">${esc(event.orderId??event.id??'—')}</td><td>${esc(event.side??'—')}</td><td>${esc(event.priceTicks??'—')}</td><td>${esc(event.size??event.quantity??'—')}</td></tr>`).join('')}</tbody></table><p class="micro-note">Últimos ${Math.min(80,events.length)} de ${events.length} eventos do arquivo. Timestamp original em nanossegundos preservado; o relógio acima exibe milissegundos.</p>`:empty('Snapshot carregado, sem eventos posteriores','A consulta de fila usa somente as ordens presentes no snapshot.');
  }
  inspectQueue(){
    if(!this.archive)return;this.error('#microMBOError',null);
    try{
      const id=this.$('#microOrderId').value.trim();if(!id)throw new Error('Informe o ID exato da ordem.');const result=queryQueue(this.archive,id);
      this.renderQueueResult(result);
    }catch(error){this.error('#microMBOError',error);}
  }
  renderQueueResult(result){
    this.queueResult=result;
    if(!result.available){this.$('#microQueueResult').innerHTML=empty('Ordem indisponível',result.reason??'O ID não está no estado final do arquivo.');return;}
    const orders=result.orders??[],offset=Math.max(0,Math.min(result.position-20,orders.length-40)),visible=orders.slice(offset,offset+40);
    this.$('#microQueueResult').innerHTML=`<div class="micro-small-metrics">${metric('Posição FIFO',String(result.position))}${metric('Ordens à frente',String(result.ordersAhead))}${metric('Quantidade à frente',String(result.quantityAhead))}${metric('Preço / lado',`${result.price} / ${result.side==='buy'?'Bid':'Ask'}`)}</div><p class="micro-note">Ordem ${esc(result.orderId)} · sequência ${esc(result.asOfSequence)} · ${nsTime(result.asOfTimeNs)} · original ${esc(result.asOfTimeNs)} ns. Quantidades e prioridade pertencem ao arquivo importado, sem verificação da bolsa. Mostrando posições ${offset+1}–${offset+visible.length} de ${orders.length} neste preço.</p><div class="micro-table-wrap"><table><thead><tr><th>Posição no nível</th><th>ID</th><th>Quantidade</th><th>Prioridade</th></tr></thead><tbody>${visible.map((order,index)=>`<tr class="${order.id===result.orderId?'micro-selected-row':''}"><td>${offset+index+1}</td><td class="micro-id" title="${esc(order.id)}">${esc(order.id)}</td><td>${esc(order.size)}</td><td>${esc(order.priority)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  downloadTemplate(){const blob=new Blob([JSON.stringify(MBO_ARCHIVE_TEMPLATE,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='QHOS-MBO-esquema-vazio.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  closedCandles(){const c=this.context,seconds=Number(c.intervalSeconds);if(!finite(seconds)||seconds<=0)return[];const until=this.cutoff();return(c.candles??[]).filter(bar=>finite(bar.time)&&!bar.partial&&(bar.time+seconds)*1000<=until);}
  canExperiment(){return providerOf(this.context)==='coinbase'&&(!this.context.instrument||this.context.instrument.assetClass==='spot');}
  renderExperimentAvailability(){
    const count=this.closedCandles().length,available=this.canExperiment();
    this.$('#microExperimentForm').hidden=!available;
    this.$('#microExperimentAvailability').textContent=available?`${count} candles fechados`:'Modelo indisponível para este instrumento';
    this.$('#microRunExperiments').disabled=!available||count<12;
    this.$('#microExperimentSource').textContent=available?`${this.sourceLabel()} · ${symbolLabel(this.context)} · ${count} candles fechados. ${count<12?'São necessários pelo menos 12 candles fechados e um período conhecido.':'Aquecimento e execução seguem a metodologia indicada no resultado.'}`:'A carteira experimental usa um modelo spot Coinbase. Para este contrato, o cálculo de margem, multiplicador e custos ainda não está habilitado.';
    if(!available){this.lastExperimentResult=null;this.$('#microExperimentResults').innerHTML=empty('Carteira experimental indisponível','Gráfico, fluxo e estudos de eventos continuam disponíveis conforme os dados recebidos.');}
  }
  numeric(id,label,min,max){const raw=this.$(id).value,value=Number(raw);if(raw===''||!finite(value)||value<min||value>max)throw new Error(`${label}: informe um valor entre ${min} e ${max}.`);return value;}
  runExperiment(){
    this.error('#microExperimentError',null);
    try{const c=this.context;if(!this.canExperiment())throw new Error('Carteira experimental disponível somente para o modelo spot Coinbase.');if(this.closedCandles().length<12)throw new Error('Aguarde pelo menos 12 candles fechados para comparar as estratégias.');const config={capital:this.numeric('#microCapital','Capital',1,1e9),quantity:this.numeric('#microQuantity','Quantidade',1e-8,1e6),feeBps:this.numeric('#microFee','Taxa',0,1000),slippageTicks:this.numeric('#microSlippage','Slippage',0,1000),tickSize:this.numeric('#microTick','Tick',1e-8,1e6)};if(!Number.isInteger(config.slippageTicks))throw new Error('Slippage deve ser um número inteiro de ticks.');const result=runExperiments({candles:c.candles??[],until:this.cutoff(),intervalSeconds:c.intervalSeconds,...config});this.renderExperimentResults(result,{...c,source:this.sourceLabel()},config);}catch(error){this.error('#microExperimentError',error);}
  }
  renderExperimentResults(result,source,config){
    this.lastExperimentResult=[result,source,config];
    if(!result.available){this.$('#microExperimentResults').innerHTML=empty('Experimento indisponível',notes(result.notes)||'O histórico não atende às condições do estudo.');return;}
    const rows=result.results??[],colors=['#b9f58b','#80a7f7','#e9ad64'],assumptions=result.assumptions??{};
    const method=[['Aquecimento',`${assumptions.warmupBars??10} candles fechados consecutivos; lacunas reiniciam a formação do sinal.`],['Estratégias','Tendência compara MA5 e MA10; reversão compara o fechamento com MA10. A referência compra após o mesmo aquecimento e mantém até o fim.'],['Execução','Sinal do candle fechado; entrada ou saída somente na abertura seguinte. A posição restante é encerrada no último fechamento disponível.'],['Capital','Limite de exposição de uma vez o capital disponível. Entradas que não cabem no saldo são rejeitadas e contadas.'],['Custos','Taxa por execução e slippage fixo. P&L antes das taxas já incorpora slippage; P&L líquido também desconta taxas.'],['Limites','Custos hipotéticos, sem fila, latência, impacto, financiamento ou aluguel. Condições históricas simples, sem validação preditiva.']];
    this.$('#microExperimentResults').innerHTML=`<div class="micro-result-heading"><strong>${esc(symbolLabel(source))} · comparação de capital</strong><span>${esc(source.source)} · até ${dateTime(assumptions.until)}</span></div>${this.equityChart(rows,colors)}<div class="micro-table-wrap"><table><thead><tr><th>Estratégia</th><th>Capital final</th><th>P&amp;L antes das taxas</th><th>P&amp;L líquido</th><th>Taxas</th><th>Slippage</th><th>Entradas / saídas</th><th>Posição final</th><th>Rejeitadas</th></tr></thead><tbody>${rows.map((row,index)=>`<tr><th><span class="micro-line-key" style="background:${colors[index%colors.length]}"></span>${esc(row.name)}</th><td>${money(row.netNAV)}</td><td>${money(row.grossPnl)}</td><td class="${row.netPnl>=0?'micro-positive':'micro-negative'}">${money(row.netPnl)}</td><td>${money(row.fees)}</td><td>${money(row.slippageCost)}</td><td>${qty(row.entries)} / ${qty(row.exits)}</td><td>${qty(row.position)}</td><td>${qty(row.rejected??0)}</td></tr>`).join('')}</tbody></table></div><p class="micro-note">Resultado fixado na execução deste experimento. Capital ${money(config.capital)} · quantidade ${qty(config.quantity)} · taxa ${num(config.feeBps,1)} bps · slippage ${config.slippageTicks} ticks de ${num(config.tickSize,8)} USD. Aquecimento de ${qty(assumptions.warmupBars??10)} candles; execução na abertura seguinte. O slippage indicado já está no P&amp;L e não deve ser subtraído novamente.</p><details class="micro-method"><summary>Premissas, custos e aquecimento</summary><dl class="micro-facts">${method.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl></details>`;
  }
  equityChart(rows,colors){
    const curves=rows.map(row=>(row.curve??[]).filter(point=>finite(point.equity)&&finite(point.time))),points=curves.flat();if(points.length<2)return'';const from=Math.min(...points.map(p=>p.time)),to=Math.max(...points.map(p=>p.time)),low=Math.min(...points.map(p=>p.equity)),high=Math.max(...points.map(p=>p.equity)),range=Math.max(high-low,Math.abs(high)*.0001,1),x=t=>22+(t-from)/(to-from||1)*596,y=v=>20+(high-v)/range*116;
    return `<div class="micro-equity"><svg viewBox="0 0 640 160" role="img" aria-label="Capital por estratégia ao longo dos candles fechados"><line x1="22" y1="140" x2="618" y2="140" stroke="#2b3744"/>${curves.map((curve,index)=>`<polyline fill="none" stroke="${colors[index%colors.length]}" stroke-width="2" vector-effect="non-scaling-stroke" points="${curve.map(point=>`${x(point.time).toFixed(2)},${y(point.equity).toFixed(2)}`).join(' ')}"/>`).join('')}</svg><div><span>${dateTime(timestamp(from))}</span><span>${dateTime(timestamp(to))}</span></div></div>`;
  }
  renderSessionOptions(){
    const select=this.$('#microStudySource'),selected=select.value;
    select.innerHTML='<option value="current">Captura atual</option>'+this.catalog.map(item=>`<option value="${esc(item.id)}">${esc(displayTime.sessionName(item))} · ${esc(item.instrument?.symbol??item.symbol??'')}</option>`).join('');
    if([...select.options].some(option=>option.value===selected))select.value=selected;
  }
  async refreshSessions(){
    if(typeof this.callbacks.listSessions!=='function')return;const button=this.$('#microRefreshSessions');button.disabled=true;
    try{const catalog=await this.callbacks.listSessions();if(this.destroyed)return;this.catalog=Array.isArray(catalog)?catalog:[];this.renderSessionOptions();}catch(error){this.error('#microStudyError',error);}finally{if(!this.destroyed)button.disabled=false;}
  }
  async runStudy(){
    this.error('#microStudyError',null);const run=++this.studyRun,button=this.$('#microRunStudy');button.disabled=true;
    try{const id=this.$('#microStudySource').value,side=this.$('#microStudySide').value,minSize=this.numeric('#microStudyMin','Quantidade mínima',0,1e6),windowMs=Number(this.$('#microStudyWindow').value);if(!['all','buy','sell'].includes(side)||![1000,5000].includes(windowMs))throw new Error('Selecione condição e janela válidas.');let source,until;if(id==='current'){source={...this.context,isCurrentStudy:true,trades:[...(this.context.trades??[])],books:[...(this.context.books??[])]};until=this.cutoff();}else{if(typeof this.callbacks.getSession!=='function')throw new Error('Biblioteca de sessões indisponível.');const saved=await this.callbacks.getSession(id);if(!saved)throw new Error('A sessão não está mais disponível.');source={...saved,replaying:true};until=finite(saved.recording?.to)?saved.recording.to:Math.max(0,...(saved.trades??[]).map(trade=>trade.time),...(saved.books??[]).map(book=>book.time));}if(this.destroyed||run!==this.studyRun)return;if(esBloqueado(source))throw new Error(AVISO_ES);const result=runEventStudy({trades:source.trades??[],books:source.books??[],until,side,minSize,windowMs,maxEvents:200});this.renderStudyResults(result,source,until,id==='current'?'Captura atual':source.name??'Sessão salva');}catch(error){this.error('#microStudyError',error);}finally{if(!this.destroyed&&run===this.studyRun)button.disabled=false;}
  }
  renderStudyResults(result,source,until,name){
    this.lastStudyResult=[result,source,until,name];
    const shownName=source.isCurrentStudy||!source.name?name:displayTime.sessionName(source);
    const summary=result.summary??{},rows=result.rows??[],canFocus=source.isCurrentStudy&&typeof this.callbacks.focusExecution==='function';
    this.$('#microStudyResults').innerHTML=`<div class="micro-result-heading"><strong>${esc(shownName)} · ${esc(symbolLabel(source))}</strong><span>${esc(this.sourceLabel(source))} · até ${dateTime(until)}</span></div><div class="micro-small-metrics">${metric('Eventos avaliados',qty(summary.events))}${metric('Com janela completa',qty(summary.complete))}${metric('Sem snapshots suficientes',qty(summary.missing))}${metric('Retorno médio',`${signed(summary.meanReturnBps)} bps`)}</div>${rows.length?`<div class="micro-table-wrap micro-study-table"><table><thead><tr><th>Evento ${displayTime.label()}</th><th>Agressor</th><th>Quantidade</th><th>Mid antes</th><th>Mid depois</th><th>Retorno</th><th>Cobertura</th>${canFocus?'<th>Gráfico</th>':''}</tr></thead><tbody>${rows.map(row=>`<tr><td>${dateTime(row.time)}</td><td class="${row.side==='buy'?'micro-positive':'micro-negative'}">${row.side==='buy'?'Compra / ask':'Venda / bid'}</td><td>${qty(row.size)}</td><td title="${esc(`Snapshot anterior observado: ${dateTime(row.preTime)}`)}">${num(row.preMid)}</td><td title="${esc(`Snapshot posterior observado: ${dateTime(row.postTime)}`)}">${num(row.postMid)}</td><td>${signed(row.returnBps)} bps</td><td class="micro-coverage" title="${esc(row.reason??'')}">${row.status==='complete'?'Completa':esc(row.reason??'Snapshot ausente')}</td>${canFocus?`<td>${row.tradeId!=null?`<button type="button" class="micro-button micro-focus-trade" data-micro-execution="${esc(`market:${source.symbol}:${row.tradeId}`)}" aria-label="Ver execução ${esc(row.tradeId)} no gráfico">Ver ↗</button>`:'—'}</td>`:''}</tr>`).join('')}</tbody></table></div>`:empty('Nenhum evento elegível','Ajuste a condição ou selecione uma sessão com negócios e snapshots.')}<p class="micro-note">Resultado fixado nesta análise. Janela ±${num((result.windowMs??0)/1000,0)} s em torno do evento · até 1,5 s de tolerância por snapshot · máximo de 200 eventos por estudo. Passe sobre os mid-prices para conferir os horários observados. ${esc(notes(result.notes))}</p>`;
  }
  destroy(){this.destroyed=true;clearTimeout(this.timer);this.studyRun++;this.mboRun++;this.heatObserver?.disconnect();this.heatmap?.destroy();}
}
