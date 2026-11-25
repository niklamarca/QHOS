'use strict';
(() => {
  // Canonical presentation only. No fetch, market collector or book reconstruction.
  const SCHEMA='qhos.rithmic.observation.v1', OPS='qhos.rithmic.operational.v1';
  const labels={empty:'Sem captura',configured:'Configurado',connecting:'Conectando',idle:'Conectado · ocioso',receiving:'Recebendo dados',processing:'Processando',recovering:'Recuperando',stale:'Dados antigos',error:'Erro',blocked:'Bloqueado',disconnected:'Desconectado',disabled:'Desabilitado',unknown:'Atividade desconhecida',recorded:'Captura histórica',unavailable:'Indisponível'};
  const components=[['source','Rithmic','Fonte de negócios e topo do livro.','connection'],['capture','Captura','Registra as mensagens recebidas.','capture'],['normalize','Normalização','Interpreta negócios e BBO.','trades / bbo'],['store','Gravação','Preserva os eventos recebidos e a projeção.','persistence'],['reader','Observação','Lê a projeção persistida.','ObservationReader.summary'],['consumers','Produtos','Dashboard, Brain e Microstructure recebem a mesma projeção.','HTTP / WS existentes']];
  const shown=v=>v==null?'—':String(v), exactPrice=v=>typeof v?.decimal==='string'?v.decimal:'—';
  function ms(ns){try{return Number(BigInt(ns)/1000000n);}catch{return NaN;}}
  function utc(ns){if(ns==null)return '—';try{const n=BigInt(ns);return new Date(Number(n/1000000n)).toISOString().slice(0,19)+'.'+(n%1000000000n).toString().padStart(9,'0')+'Z';}catch{return 'Horário indisponível';}}
  function recent(ns,now,limit=5000){const age=now-ms(ns);return Number.isFinite(age)&&age>=0&&age<=limit;}
  function positive(v){try{return BigInt(v)>0n;}catch{return false;}}
  function model(s,now=Date.now(),failure='',delivery=null){
    const valid=!!s&&s.schema_version===SCHEMA&&s.research_only===true&&s.can_execute===false&&s.book_valid===false&&s.quality?.available_as_live===false&&s.connection?.connected===false&&s.subscription?.active===false&&['RECORDED','EMPTY','ERROR','UNAVAILABLE'].includes(s.status)&&['historical_capture','synthetic_fixture','none'].includes(s.source_kind);
    const op=valid&&s.operational?.schema_version===OPS?s.operational:null, historical=valid&&s.source_kind==='historical_capture';
    const stale=!!failure||s?.telemetry?.stale===true||!!s?.telemetry?.error, fresh=op&&recent(op.observed_ns,now,Math.min(12000,op.freshness_ms||12000));
    const provenance=op||s||{},kind=provenance.source_kind||'none';
    // The feed taxonomy, defaulted rather than assumed. A summary written before this section
    // existed has no classification, and 'unknown' is what that means - not 'production'.
    const fc=provenance.feed_classification||s?.feed_classification||{};
    const envKind=fc.environment_kind||'unknown';
    const envLabel={production:'Produção',vendor_test:'Ambiente de teste do fornecedor · não é mercado de produção',local:'Local · sintético',unknown:'Classificação não estabelecida'}[envKind]||'Classificação não estabelecida';
    const modalityLabel={realtime:'tempo real',delayed:'atrasado',replay:'replay',synthetic:'sintético',unproven:'não comprovado',vendor_test:'dados de teste do fornecedor'}[fc.modality]||'não comprovado';
    const blocked=s?.status==='ERROR'||s?.status==='UNAVAILABLE'||(op?.quality?op.quality.available_for_observation===false:s?.quality?.available_for_observation===false);const bloqQ=(op?.quality?op.quality.available_for_observation===false:s?.quality?.available_for_observation===false);
    const operational=!!(fresh&&!stale&&!blocked&&op.quality?.stale!==true&&['current_session','replay','fixture'].includes(kind)&&op.capture_finished!==true);
    const reasons=op?.quality?.blocked_reasons||s?.quality?.blocked_reasons;
    const reason=failure||s?.telemetry?.error||(!valid?'Contrato de observação ausente ou incompatível.':Array.isArray(reasons)?reasons.join(' · '):typeof reasons==='string'?reasons:'');
    const nodes=components.map(([id,title,role,origin])=>{
      const srv=op?.nodes?.[id];const raw=delivery&&['reader','consumers'].includes(id)&&!['error','blocked','disconnected','disabled','stale','recovering','unavailable','recorded'].includes(srv?.state)?delivery:srv;let state='unknown',count=null,last=op?null:s?.last_update?.received_ns,note='Componente no percurso; atividade atual não informada.';
      if(!valid){state='unavailable';note=reason;}
      else if(historical&&!op){state=['normalize','store','reader','consumers'].includes(id)?'recorded':'disconnected';note='Captura encerrada. Esta leitura não confirma atividade atual.';}
      if(id==='store'&&valid&&!op){count=s.persistence?.events;if(s.persistence?.verified===false&&historical){state='error';note='Integridade da persistência não confirmada.';}}
      if(id==='normalize'&&valid&&!op)count=s.trades?.incremental_count;
      if(s?.status==='ERROR'){state='error';note=reason||'Falha na leitura da observação.';}
      if(raw&&Object.hasOwn(labels,raw.state)){state=raw.state;count=raw.count??null;last=raw.last_activity_ns;note=raw.reason||'Estado informado pela telemetria operacional.';}
      if(op&&!fresh&&state!=='error'){state='stale';note='Telemetria operacional sem atualização.';}
      if(op&&(op.capture_finished===true||kind==='historical_capture')&&state!=='error'){state='recorded';note='Captura encerrada; referência histórica.';}
      if(stale&&state!=='error'){state='stale';note=reason||'Telemetria desatualizada; última observação preservada.';}
      if(s?.status==='ERROR'){state='error';note=reason||'Falha na leitura da observação.';}else if(bloqQ){if(state==='error'){}else if(raw?.state==='error'){state='error';note=raw.reason||reason||'Erro informado pelo servidor.';}else{state='blocked';note=reason||'Observação bloqueada pelo backend.';}}
      if(s?.status==='UNAVAILABLE'&&!['error','blocked','stale'].includes(state)){state='unavailable';note=reason||'Observação indisponível.';}
      if(op?.persistence?.verified===false&&op?.persistence?.write_confirmed!==true&&['store','reader','consumers'].includes(id)){state='error';note='Persistência não íntegra; entrega suspensa.';}
      const a=raw?.activity,windowOK=a&&recent(a.end_ns,now)&&ms(a.end_ns)>ms(a.start_ns)&&ms(a.end_ns)-ms(a.start_ns)<=5000;
      const active=!!(operational&&['receiving','processing'].includes(state)&&windowOK&&positive(a.count));
      if(['receiving','processing'].includes(state)&&!active){state=operational?'unknown':'stale';note='Atividade recente sem janela de observação comprovada.';}
      return {id,title,role,origin:raw?.source||origin,state,count,last,note,active,activity:a||null,servidorPublicado:srv?(Object.hasOwn(labels,srv.state)?srv.state:'unknown'):null,servidor:srv?(()=>{const st=Object.hasOwn(labels,srv.state)?srv.state:'unknown';if(!['receiving','processing'].includes(st))return st;const w=srv.activity,okw=w&&recent(w.end_ns,now)&&ms(w.end_ns)>ms(w.start_ns)&&ms(w.end_ns)-ms(w.start_ns)<=5000&&positive(w.count);return operational&&okw?st:(operational?'unknown':'stale');})():null,servidorFonte:srv?.source||null,doCliente:!!delivery&&raw===delivery};
    });
    const edges=nodes.slice(1).map((n,i)=>({from:nodes[i].id,to:n.id,active:operational&&nodes[i].active&&n.active&&Array.isArray(op.edges)&&(op.edges.some(e=>e?.from===nodes[i].id&&e?.to===n.id&&e.observed===true)||delivery&&['reader','consumers'].includes(n.id))}));
    /* ---- estado global + regra que o decidiu (REGRAS_ESTADOS.md) ----
       Precedencia: indisponivel > erro > bloqueado > stale > recuperacao >
       processando > recebendo > estado do no de origem (conectando, ocioso,
       configurado, desconectado, encerrado, desconhecido). */
    const [state,regra]=(()=>{
      const R=(id,st,campo,valor,motivo)=>[st,{id,campo,valor:String(valor),motivo:String(motivo||'')}];
      const achou=(v)=>nodes.find(n=>n.state===v);
      if(!valid)return R('R01','unavailable',failure?'leitura da API':'schema_version',failure||(s?.schema_version??'ausente'),failure?'A leitura da API falhou; nenhuma observação válida em mãos.':'Contrato de observação ausente ou incompatível.');
      if(op){
        /* REVISAO 01/10: o global NUNCA e melhor que o PIOR no do servidor
           (ordem PIOR abaixo). Para cada no conta o pior entre o que a tela
           mostra e o que o servidor disse; num no cuja tela veio da entrega do
           cliente (doCliente, decidido por identidade nesta pagina) conta SO o servidor: a entrega
           nunca melhora nem apaga nada. Unica exclusao, declarada: reader e
           consumers que o servidor publica como unknown com fonte
           awaiting_product_read (o servidor diz que nao observa a leitura do
           produto); qualquer outro unknown deles conta. */
        const PIOR=['error','unavailable','blocked','disconnected','disabled','stale','recorded','recovering','unknown','connecting','configured','empty','idle','receiving','processing'];
        const rk=v=>{const i=PIOR.indexOf(v);return i<0?PIOR.indexOf('unknown'):i;};
        const efetivo=n=>n.doCliente?(n.servidor||'unknown')
          :[n.state,n.servidor].filter(Boolean).reduce((p,v)=>rk(v)<rk(p)?v:p);
        /* revisao v2 (N1): a exclusao olha o estado PUBLICADO (unknown + awaiting_product_read),
           nunca o efetivo; e nao exclui um no cuja tela ja mostra algo pior que unknown
           (bloqueio, telemetria velha, persistencia nao integra...) */
        const naoObservado=n=>['reader','consumers'].includes(n.id)&&n.servidorPublicado==='unknown'&&n.servidorFonte==='awaiting_product_read'
          &&(n.doCliente||rk(n.state)>=rk('unknown'));
        const contam=nodes.filter(n=>!naoObservado(n));
        let n=contam[0];for(const x of contam)if(rk(efetivo(x))<rk(efetivo(n)))n=x;
        const v=PIOR.includes(efetivo(n))?efetivo(n):'unknown';
        if(stale&&rk('stale')<=rk(v))return R('R04','stale',failure?'leitura da API':'telemetry.stale',failure||'true',reason||'Leitura falhou; a última observação válida foi preservada.');
        const campo='nodes.'+n.id+'.state';
        if(v==='error')return R('R02','error',s?.status==='ERROR'?'status':campo,s?.status==='ERROR'?'ERROR':'error',n.note);
        if(v==='blocked')return R('R03','blocked','quality.available_for_observation','false',reason||'Observação bloqueada pelo backend.');
        if(v==='unavailable'||v==='disabled')return R('R17',v,campo,v,n.note);
        if(v==='stale')return R('R05','stale',!fresh?'operational.observed_ns':campo,!fresh?'fora de '+Math.min(12000,op.freshness_ms||12000)+' ms':'stale',n.note);
        if(v==='processing'||v==='receiving'){
          /* o pior no esta processando/recebendo: TODOS os que contam estao assim */
          const q=String(n.activity?.count??'?')+' em janela ≤ 5 s · origem '+(n.origin||'?');
          return R(v==='processing'?'R07':'R08',v,'nodes.'+n.id+'.activity.count',q,n.note);}
        const mapa={recovering:'R06',connecting:'R09',idle:'R10',configured:'R11',disconnected:'R12',recorded:'R13',unknown:'R14',empty:'R14'};
        return R(mapa[v]||'R14',v,campo,v,n.note);
      }
      const e=achou('error');if(e)return R('R02','error',s?.status==='ERROR'?'status':'nodes.'+e.id+'.state',s?.status==='ERROR'?'ERROR':'error',e.note);
      if(achou('blocked'))return R('R03','blocked','quality.available_for_observation','false',reason||'Observação bloqueada pelo backend.');
      if(stale)return R('R04','stale',failure?'leitura da API':'telemetry.stale',failure||'true',reason||'Leitura falhou; a última observação válida foi preservada.');
      if(s.status==='RECORDED')return R('R13','recorded','status','RECORDED','Captura encerrada; referência histórica.');
      if(s.status==='UNAVAILABLE')return R('R15','unavailable','status','UNAVAILABLE',reason||'Observação indisponível.');
      if(s.status==='EMPTY')return R('R16','empty','status','EMPTY','A fonte respondeu e não há registro.');
      return R('R14','unknown','status',s.status,'Nenhuma evidência estabelece o estado.');
    })();
    return {valid,state,regra,nodes,edges,reason,operational,source:provenance.provider||s?.provider||'Rithmic',environment:provenance.environment||'Ambiente desconhecido',envKind,envLabel,modalityLabel,productionData:fc.production_market_data===true,classificationBasis:fc.basis||'not_established',kind,instrument:op?(op.instrument||{}):(s?.instrument||{}),period:utc(op?op.observed_ns:s?.last_update?.received_ns),historical:historical&&!op,fixture:['fixture','synthetic_fixture'].includes(kind),summary:valid?s:{},op};
  }
  function deliveryEvidence(value,previous,now,previousAt){
    const a=value?.operational,b=previous?.operational;
    if(!a||!b||typeof a.session_id!=='string'||!a.session_id||typeof a.run_id!=='string'||!a.run_id||a.session_id!==b.session_id||a.run_id!==b.run_id||a.observed_ns===b.observed_ns||!recent(a.observed_ns,now,5000)||now<=previousAt||now-previousAt>5000)return null;
    const total=a.nodes?.store?.count,prior=b.nodes?.store?.count;
    if(!Number.isSafeInteger(total)||!Number.isSafeInteger(prior)||total<=prior)return null;
    return {state:'processing',count:total,last_activity_ns:String(BigInt(now)*1000000n),source:'product_received_projection',reason:'Novos eventos registrados chegaram à visualização.',activity:{start_ns:String(BigInt(previousAt)*1000000n),end_ns:String(BigInt(now)*1000000n),count:total-prior}};
  }
  /* E8-07: estado publicado do livro DBO de teste (contrato qhos.dbo.test-book.v4, Etapa 3 v4r3).
     Regras D01-D10 em REGRAS_ESTADOS.md. Desconhecido nunca melhora; book_valid e sempre falso. */
  const DBO_ESTADOS=['WAITING_SUBSCRIPTION','WAITING_SNAPSHOT','SNAPSHOT_RECEIVING','REPLAYING_BUFFER','SYNCHRONIZED','SYNCHRONIZED_EMPTY','INVALID','BLOCKED','CLOSED'];
  const DBO_SINC=['SYNCHRONIZED','SYNCHRONIZED_EMPTY'];
  function dboLivro(doc,agoraMs,maxIdadeMs=5000){
    const fixo='Rithmic Test é dado de teste — nunca profundidade real da CME · book_valid = false';
    const origem=typeof doc?.labels?.display_label==='string'?doc.labels.display_label+' · ':'';
    const R=(estado,rotulo,regra,motivo)=>({estado,rotulo,regra,motivo:motivo+' · '+origem+fixo});
    if(!doc||typeof doc!=='object')return R('unknown','Livro: desconhecido','D01','documento ausente ou ilegível');
    if(doc.schema!=='qhos.dbo.test-book.v4')return R('unknown','Livro: contrato desconhecido','D01','schema = '+String(doc.schema));
    if(typeof doc.state!=='string'||!DBO_ESTADOS.includes(doc.state))return R('unknown','Livro: estado desconhecido','D02','state = '+String(doc.state)+' fora do contrato v4 — nunca tratado como coerente');
    /* D02, mesma posicao da tabela: contradicoes do documento tambem sao desconhecido */
    if(doc.book_coherent===true&&!DBO_SINC.includes(doc.state))return R('unknown','Livro: estado desconhecido','D02','book_coherent = true com state = '+doc.state+' — contrato violado');
    /* Etapa 3 v4r4 (06/10): vazio = orders, bids e asks TODOS listas vazias; coerente = alguma nao vazia.
       Qualquer um ausente ou fora de lista = nao da para afirmar nem vazio nem com ordens. */
    const tam=v=>Array.isArray(v)?v.length:null,nOrdens=tam(doc.orders),nB=tam(doc.bids),nA=tam(doc.asks);
    const contavel=nOrdens!==null&&nB!==null&&nA!==null,temOrdens=contavel&&(nOrdens+nB+nA)>0;
    const lado='orders '+(nOrdens??'ausente')+' · bids '+(nB??'ausente')+' · asks '+(nA??'ausente');
    if(doc.book_coherent===true&&doc.state==='SYNCHRONIZED_EMPTY'&&(!contavel||temOrdens))return R('unknown','Livro: estado desconhecido','D02','state = SYNCHRONIZED_EMPTY com '+lado+' — contradição, nunca "vazio"');
    if(doc.book_coherent===true&&doc.state==='SYNCHRONIZED'&&!temOrdens)return R('unknown','Livro: estado desconhecido','D02','state = SYNCHRONIZED com '+lado+' — contradição, nunca "coerente"');
    if(doc.book_valid!==false||doc.can_execute!==false)return R('error','Livro: contrato violado','D03','book_valid/can_execute diferente de false');
    if(doc.state==='CLOSED')return R('disconnected','Livro: encerrado','D04','state = CLOSED');
    if(doc.state==='BLOCKED')return R('blocked','Livro: bloqueado','D05','state = BLOCKED');
    const pub=Number(doc.times?.published_wall_ns)/1e6,idade=agoraMs-pub;
    if(!Number.isFinite(pub)||!(idade>=0)||idade>maxIdadeMs)return R('stale','Livro: publicação fora do prazo','D06','times.published_wall_ns '+(Number.isFinite(pub)?(idade<0?'no futuro':'há '+Math.round(idade/1000)+' s'):'ausente'));
    if(['WAITING_SUBSCRIPTION','WAITING_SNAPSHOT','SNAPSHOT_RECEIVING','REPLAYING_BUFFER'].includes(doc.state))return R('connecting','Livro: sincronizando','D07','state = '+doc.state+' · ainda não coerente');
    if(doc.book_coherent!==true)return R('error','Livro inválido','D08','state = '+doc.state+' · book_coherent = '+String(doc.book_coherent)+(doc.coherence_reason?' · '+String(doc.coherence_reason):''));
    if(doc.state==='SYNCHRONIZED_EMPTY')return R('empty','Livro vazio (dado de teste)','D09','state = SYNCHRONIZED_EMPTY · sincronizado, coerente, orders, bids e asks vazios (0 ordens)');
    if(doc.state==='SYNCHRONIZED')return R('ready','Livro coerente (dado de teste)','D10','state = SYNCHRONIZED · coerente, '+lado+', não validado');
    return R('unknown','Livro: estado desconhecido','D02','state = '+String(doc.state));
  }
  if(typeof document==='undefined'){if(typeof module!=='undefined')module.exports={model,labels,utc,exactPrice,deliveryEvidence,dboLivro};return;}
  const roots=new Map(),reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)');
  let latest=null,delivery=null,receivedAt=0,failure='',timer=null,drawTimer=null,lastDraw=0,destroyed=false,paused=false;
  try{paused=localStorage.getItem('qhos-motion-paused')==='true';}catch{}
  function put(el,v){const text=shown(v);if(el&&el.textContent!==text)el.textContent=text;}
  const fieldNames=['Função','Estado','Última atividade · UTC','Fonte da informação','Contador informado','Motivo / limite','Origem · ambiente','Instrumento / bolsa','Período · UTC','Assinatura','Persistência','Capacidades','Captura / sessão','Recepção da captura · UTC','Telemetria dos produtos','Hipóteses','Idade do dado','Negócios / snapshots / parciais','Reconexões / interrupções','Recuperação registrada','Condições da observação','Classificação do feed','Mercado de produção'];
  function create(root){
    root.innerHTML='<div class="ro-heading"><div><span class="ro-eyebrow">PERCURSO DOS DADOS · SOMENTE PESQUISA</span><h2>Rithmic <span>· observação</span></h2></div><button type="button" class="ro-motion">Pausar movimento</button></div><div class="ro-context"><strong class="ro-status" role="status"></strong><span class="ro-regra"></span><span class="ro-environment"></span><span class="ro-symbol"></span><span class="ro-kind"></span></div><div class="ro-flow" aria-label="Componentes do percurso dos dados"></div><div class="ro-dbo" hidden><span class="ro-dbo-titulo">LIVRO DBO · TESTE</span><strong class="ro-dbo-estado"></strong><span class="ro-dbo-nota"></span></div><div class="ro-bottom"><span class="ro-caption"></span><span>Pesquisa · execução bloqueada</span></div><div class="ro-bbo" hidden><div><span>Melhor compra</span><b data-bid>—</b><small data-bid-size>—</small></div><div><span>Melhor venda</span><b data-ask>—</b><small data-ask-size>—</small></div><p class="ro-book-note"></p></div><details class="ro-detail"><summary>Inspecionar componente e origem</summary><h3 class="ro-selected"></h3><dl class="ro-facts"></dl><p class="ro-limit">BBO não demonstra profundidade ou fila. Brain é observador de pesquisa, sem autorização de execução.</p></details>';
    const buttons=new Map(),links=[],fields=[],flow=root.querySelector('.ro-flow');
    components.forEach(([id,title],i)=>{if(i){const link=document.createElement('span');link.className='ro-link';link.setAttribute('aria-hidden','true');link.innerHTML='<i></i>';flow.append(link);links.push(link);}const b=document.createElement('button');b.type='button';b.className='ro-node';b.dataset.node=id;b.innerHTML='<i aria-hidden="true"></i><span><b></b><small></small></span>';put(b.querySelector('b'),title);b.onclick=()=>select(id,root);flow.append(b);buttons.set(id,b);});
    fieldNames.forEach(name=>{const div=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');put(dt,name);div.append(dt,dd);root.querySelector('.ro-facts').append(div);fields.push(dd);});
    roots.set(root,{root,buttons,links,fields,selected:'source'});root.querySelector('.ro-motion').onclick=()=>setMotion(!paused);
  }
  function select(id,target){for(const [root,r]of roots){if(target&&target!==root||!r.buttons.has(id))continue;r.selected=id;root.querySelector('details').open=true;}paint();}
  function motionPaused(){return paused||!!reduced?.matches;}
  function setMotion(v){paused=!!v;try{localStorage.setItem('qhos-motion-paused',String(paused));}catch{}motionChange();}
  function getModel(){return model(latest,Date.now(),failure||(receivedAt&&Date.now()-receivedAt>12000?'API sem atualização; última observação preservada.':''),delivery);}
  function paint(){
    if(destroyed||document.hidden)return;const m=getModel();
    document.querySelectorAll('[data-rithmic-observation]').forEach(root=>{if(!roots.has(root))create(root);});
    for(const [root,r]of roots){
      if(!root.isConnected){roots.delete(root);continue;}
      root.dataset.state=m.state;root.dataset.motion=motionPaused()?'off':'on';root.dataset.fixture=String(m.fixture);
      {/* Estado global como SELO do design system: glifo + rotulo + idade do dado.
   Antes era texto colorido, e cinco estados dividiam a mesma cor. */
const st=root.querySelector('.ro-status'),U=window.QHOSUI;
const ds=({recorded:'stale'})[m.state]||m.state;
const tsDado=ms(m.op?.last_update?.received_ns??m.summary?.last_update?.received_ns);
if(U&&st){U.badge(st,U.normaliza(ds)===ds?ds:'unknown',{label:labels[m.state]||undefined,reason:m.regra?m.regra.id+' · '+m.regra.motivo:m.reason,ts:Number.isFinite(tsDado)?tsDado:undefined,live:true});st.dataset.estado=m.state;}
else put(st,labels[m.state]);
/* A regra que decidiu, em texto de verdade: campo e valor RECEBIDOS. */
put(root.querySelector('.ro-regra'),m.regra?m.regra.id+' · '+m.regra.campo+' = '+m.regra.valor+' · '+m.regra.motivo:'');}
{/* E8-07: linha do livro DBO, so quando a observacao traz o documento */const box=root.querySelector('.ro-dbo'),doc=latest?latest.dbo_test_book:undefined,U2=window.QHOSUI;if(box){const L=doc===undefined||doc===null?null:dboLivro(doc,Date.now());box.hidden=!L;box.dataset.estado=L?L.estado:'';box.dataset.regra=L?L.regra:'';if(L){const e=box.querySelector('.ro-dbo-estado');if(U2&&e)U2.badge(e,L.estado,{label:L.rotulo,reason:L.regra+' · '+L.motivo,live:true});else put(e,L.rotulo);put(box.querySelector('.ro-dbo-nota'),L.regra+' · '+L.motivo);}}}put(root.querySelector('.ro-environment'),m.source+' · '+m.environment+' · '+m.envLabel);put(root.querySelector('.ro-symbol'),shown(m.instrument.symbol)+' / '+shown(m.instrument.exchange));
      put(root.querySelector('.ro-kind'),({none:'Sem captura',historical_capture:'Histórico · não é feed atual',replay:'Replay · histórico em reprodução',current_session:'Sessão atual',fixture:'FIXTURE DE DESENVOLVIMENTO',synthetic_fixture:'FIXTURE SINTÉTICA · sem mercado real'}[m.kind]||'Origem desconhecida'));
      const motion=root.querySelector('.ro-motion');put(motion,motionPaused()?'Movimento reduzido':'Pausar movimento');motion.setAttribute('aria-pressed',String(motionPaused()));
      m.nodes.forEach(n=>{const b=r.buttons.get(n.id);b.dataset.state=n.state;b.dataset.active=String(n.active);b.setAttribute('aria-pressed',String(r.selected===n.id));put(b.querySelector('small'),labels[n.state]);b.title=n.note;});
      m.edges.forEach((e,i)=>r.links[i].dataset.active=String(e.active));put(root.querySelector('.ro-caption'),m.nodes.some(n=>n.active)?'Atividade observada · agregada em janelas de até 5 s.':'Sem tráfego recente comprovado · linhas indicam o percurso.');
      const n=m.nodes.find(n=>n.id===r.selected),s=m.op?{...m.op,quality:m.summary.quality}:m.summary;put(root.querySelector('.ro-selected'),n.title);
      const values=[n.role,labels[n.state],utc(n.last),n.origin,shown(n.count),n.note,m.source+' · '+m.environment,shown(m.instrument.symbol)+' / '+shown(m.instrument.exchange),m.period,s.subscription?.active===true?'Ativa':s.subscription?.confirmed?'Confirmada na captura histórica':s.subscription?.state||'Não informada',shown(s.persistence?.state)+' · integridade '+(s.persistence?.verified===true?'verificada':s.persistence?.verified===false?'não confirmada':'desconhecida'),'Negócios / BBO conforme campos recebidos · profundidade e MBO indisponíveis',shown(s.capture?.capture_id)+' / '+shown(s.capture?.session_id),utc(s.last_update?.received_ns),s.telemetry?.stale?'Antiga':s.telemetry?.transport||'Leitura pela API do produto','Nenhuma hipótese é produzida por esta visualização.'];
      values.push(s.last_update?.age_seconds==null?'—':String(s.last_update.age_seconds)+' s desde a recepção local',shown(s.trades?.incremental_count)+' / '+shown(s.trades?.snapshot_count)+' / '+shown(s.trades?.partial_count),shown(s.continuity?.reconnects)+' / '+shown(s.continuity?.discontinuities),s.continuity?.recovery_observed===true?'Observada · continuidade integral não comprovada':s.continuity?.recovery_observed===false?'Não observada':'Não informada',m.reason||'Sem motivo adicional informado',m.envLabel+' · modalidade '+m.modalityLabel,m.productionData?'—':'Não · qualificação de produção não demonstrada'+(m.classificationBasis&&m.classificationBasis!=='not_established'?' · base '+m.classificationBasis:''));
      r.fields.forEach((el,i)=>put(el,values[i]));
      const bbo=root.querySelector('.ro-bbo');bbo.hidden=root.dataset.product!=='microstructure';
      if(!bbo.hidden){const util=b=>!!b&&(typeof b.bid_price?.decimal==='string'||typeof b.ask_price?.decimal==='string');const opBBO=m.op?m.op.bbo:null,resumoBBO=(m.summary&&m.summary.bbo)||null;const usaResumo=!util(opBBO)&&util(resumoBBO);const current=(usaResumo?resumoBBO:opBBO)||{};bbo.dataset.qhosFonte=usaResumo?'projecao':m.op?'sessao':'projecao';for(const side of ['bid','ask']){const cell=bbo.querySelector('[data-'+side+']'),v=exactPrice(current[side+'_price']),changed=cell.textContent!==v&&cell.textContent!=='—';put(cell,v);put(bbo.querySelector('[data-'+side+'-size]'),shown(current[side+'_size'])+' contratos');if(changed&&m.nodes[0].active&&!motionPaused()){cell.classList.remove('ro-changed');void cell.offsetWidth;cell.classList.add('ro-changed');}}const instBBO=usaResumo?((m.summary&&m.summary.instrument)||{}):((m.op&&m.op.instrument)||(m.summary&&m.summary.instrument)||{});bbo.dataset.qhosHistorico=usaResumo?'1':'0';const _hist=ms(m.summary?.last_update?.received_ns),_U=window.QHOSUI;const _idade=Number.isFinite(_hist)&&_U?' · recebido há '+_U.idade(_hist,Date.now()).texto+' ('+_U.utcExato(_hist)+')':' · horário de recepção não informado';put(bbo.querySelector('.ro-book-note'),(usaResumo&&m.op?'HISTÓRICO — não é o topo de livro atual. A sessão corrente não trouxe topo de livro ('+((opBBO&&opBBO.condition)||'sem condition')+'); exibindo o último BBO persistido de '+(instBBO.symbol||'instrumento não informado')+_idade+'.':m.operational?'Topo do livro informado pelo backend.':'Último BBO observado · referência histórica / atualidade não confirmada.')+' Profundidade e ordens individuais indisponíveis.');}
    }
    window.dispatchEvent(new CustomEvent('qhos-rithmic-visual',{detail:m}));lastDraw=performance.now();
  }
  function render(value){if(destroyed)return;delivery=deliveryEvidence(value,latest,Date.now(),receivedAt);latest=value;receivedAt=Date.now();failure='';if(document.hidden)return;if(performance.now()-lastDraw>=250)paint();else if(!drawTimer)drawTimer=setTimeout(()=>{drawTimer=null;paint();},250);if(!timer)timer=setInterval(paint,1000);}
  function unavailable(reason='API indisponível; última observação preservada.'){failure=reason;paint();}
  function visibility(){if(document.hidden){clearInterval(timer);clearTimeout(drawTimer);timer=drawTimer=null;}else{paint();if(!timer)timer=setInterval(paint,1000);}}
  function motionChange(){paint();window.dispatchEvent(new CustomEvent('qhos-motion',{detail:{paused:motionPaused()}}));}
  function dispose(){destroyed=true;clearInterval(timer);clearTimeout(drawTimer);timer=drawTimer=null;document.removeEventListener('visibilitychange',visibility);reduced?.removeEventListener?.('change',motionChange);roots.clear();}
  document.addEventListener('visibilitychange',visibility);reduced?.addEventListener?.('change',motionChange);window.addEventListener('pagehide',dispose,{once:true});window.addEventListener('pageshow',e=>{if(e.persisted)location.reload();});
  window.QHOSRithmicObservation={render,unavailable,model,labels,utc,exactPrice,dboLivro,select,setMotion,motionPaused,getModel,dispose};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',paint,{once:true});else paint();
})();
/* ==========================================================================
   QHOS UI v1 — vocabulario comum de estados, idade do dado e tooltips.
   Acompanha o helper canonico acima e e servido pelos mesmos tres endpoints.
   NAO possui estado de mercado, NAO faz fetch, NAO cria timer periodico.
   Os unicos ouvintes sao delegados no document (tooltip) e um listener de
   prefers-reduced-motion; ambos sao removidos em pagehide.
   ========================================================================== */
(() => {
  'use strict';
  if (typeof document === 'undefined') return;
  if (window.QHOSUI) return;

  // --- 1. Vocabulario de estados -----------------------------------------
  // glifo: nunca dependemos so da cor. tone: apenas a familia visual.
  const STATES = {
    loading:      {label: 'Carregando',        glyph: '◌', hint: 'A leitura foi pedida e ainda nao voltou.'},
    configured:   {label: 'Configurado',       glyph: '⚙', hint: 'Parametros definidos; nenhuma conexao iniciada.'},
    connecting:   {label: 'Conectando',        glyph: '↻', hint: 'Handshake em andamento; nada foi recebido ainda.'},
    idle:         {label: 'Conectado · ocioso', glyph: '○', hint: 'Conexao aberta e sem trafego no periodo observado.'},
    receiving:    {label: 'Recebendo',         glyph: '◆', hint: 'Eventos chegaram dentro da janela de observacao.'},
    processing:   {label: 'Processando',       glyph: '◇', hint: 'Eventos recebidos estao sendo interpretados.'},
    recovering:   {label: 'Recuperando',       glyph: '↺', hint: 'Reconexao apos interrupcao; continuidade nao comprovada.'},
    ready:        {label: 'Pronto',            glyph: '✓', hint: 'Leitura completa e dentro do prazo de frescor.'},
    stale:        {label: 'Dados antigos',     glyph: '⧖', hint: 'A ultima observacao existe mas esta fora do prazo de frescor.'},
    empty:        {label: 'Vazio',             glyph: '∅', hint: 'A fonte respondeu e nao ha nenhum registro.'},
    unavailable:  {label: 'Indisponivel',      glyph: '⊘', hint: 'A leitura nao pode ser obtida agora.'},
    disconnected: {label: 'Desconectado',      glyph: '✕', hint: 'A conexao caiu ou foi encerrada.'},
    error:        {label: 'Erro',              glyph: '⚠', hint: 'A leitura falhou; o motivo tecnico acompanha o estado.'},
    disabled:     {label: 'Desabilitado',      glyph: '⊖', hint: 'O componente existe e esta desligado por configuracao.'},
    blocked:      {label: 'Bloqueado',         glyph: '⛔', hint: 'Uma trava impede a operacao; ela permanece visivel de proposito.'},
    unknown:      {label: 'Desconhecido',      glyph: '?',      hint: 'Nenhuma evidencia estabelece o estado. Nao e o mesmo que normal.'},
  };
  const ordem = Object.keys(STATES);
  const normaliza = (s) => (typeof s === 'string' && Object.hasOwn(STATES, s) ? s : 'unknown');

  // --- 2. Tempo: idade, data efetiva, formatos exatos ---------------------
  const nsParaMs = (ns) => {
    if (ns == null) return NaN;
    try { return Number(BigInt(ns) / 1000000n); } catch { return NaN; }
  };
  function utcExato(ms) {
    if (!Number.isFinite(ms)) return '—';
    return new Date(ms).toISOString().replace('T', ' ').slice(0, 19) + 'Z';
  }
  function dataEfetiva(ms) {
    if (!Number.isFinite(ms)) return '—';
    return new Date(ms).toISOString().slice(0, 10);
  }
  // Idade legivel; devolve tambem se ela cabe no prazo informado.
  function idade(ms, agora = Date.now(), prazoMs = null) {
    if (!Number.isFinite(ms)) return {texto: 'idade desconhecida', fresh: 'unknown', ms: NaN};
    const d = agora - ms;
    if (d < 0) return {texto: 'timestamp no futuro', fresh: 'unknown', ms: d};
    const s = Math.floor(d / 1000);
    let texto;
    if (s < 60) texto = s + ' s';
    else if (s < 3600) texto = Math.floor(s / 60) + ' min';
    else if (s < 86400) texto = Math.floor(s / 3600) + ' h ' + Math.floor((s % 3600) / 60) + ' min';
    else texto = Math.floor(s / 86400) + ' d ' + Math.floor((s % 86400) / 3600) + ' h';
    const fresh = prazoMs == null ? 'unknown' : (d <= prazoMs ? 'yes' : 'no');
    return {texto, fresh, ms: d};
  }

  // --- 3. Badge de estado -------------------------------------------------
  // Escreve num elemento .qhos-state existente ou cria um novo.
  // Nunca inventa timestamp: se ts nao vier, o campo simplesmente nao aparece.
  function badge(alvo, estado, opcoes = {}) {
    const chave = normaliza(estado);
    const def = STATES[chave];
    const el = alvo || document.createElement('span');
    if (!el.classList.contains('qhos-state')) el.classList.add('qhos-state');
    el.dataset.qhosState = chave;
    const rotulo = opcoes.label || def.label;
    const titulo = opcoes.reason ? rotulo + ' — ' + opcoes.reason : rotulo + ' — ' + def.hint;
    // role=status so onde o estado muda sozinho; caso contrario e texto comum.
    if (opcoes.live !== false) el.setAttribute('role', 'status');
    el.setAttribute('aria-label', titulo);
    el.title = titulo;
    // Montagem por nos, com textContent: nenhum valor recebido vira markup.
    const pecas = [
      ['span', 'qhos-glyph', def.glyph, {'aria-hidden': 'true'}],
      ['span', 'qhos-label', rotulo, {}],
    ];
    if (Number.isFinite(opcoes.ts)) {
      const i = idade(opcoes.ts, opcoes.agora || Date.now(), opcoes.prazoMs ?? null);
      pecas.push(['time', 'qhos-age', i.texto,
                  {'data-qhos-fresh': i.fresh, datetime: new Date(opcoes.ts).toISOString()}]);
    } else if (opcoes.ageText) {
      pecas.push(['span', 'qhos-age', opcoes.ageText,
                  {'data-qhos-fresh': opcoes.fresh || 'unknown'}]);
    }
    const assinatura = JSON.stringify(pecas);
    if (el.dataset.qhosSig !== assinatura) {
      el.dataset.qhosSig = assinatura;
      el.replaceChildren(...pecas.map(([tag, cls, texto, attrs]) => {
        const n = document.createElement(tag);
        n.className = cls;
        n.textContent = String(texto);
        for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
        return n;
      }));
    }
    return el;
  }
  // Linha de motivo tecnico, opcional, sempre textual.
  function motivo(alvo, texto) {
    if (!alvo) return null;
    const t = texto == null || texto === '' ? '' : String(texto);
    if (!t) { alvo.hidden = true; alvo.textContent = ''; return alvo; }
    alvo.hidden = false;
    if (alvo.textContent !== t) alvo.textContent = t;
    if (!alvo.classList.contains('qhos-reason')) alvo.classList.add('qhos-reason');
    return alvo;
  }

  // --- 4. Ausencia: nunca zero no lugar de "nao existe" -------------------
  // valor(n) devolve o texto quando o numero existe de fato, e marca ausencia
  // quando ele nao existe. 0 so aparece quando o backend enviou 0.
  function valor(n, {casas = null, sufixo = '', ausente = 'sem dado'} = {}) {
    if (n == null || n === '' || (typeof n === 'number' && !Number.isFinite(n))) {
      return {texto: ausente, ausente: true};
    }
    const num = typeof n === 'number' ? n : Number(n);
    if (!Number.isFinite(num)) return {texto: String(n) + sufixo, ausente: false};
    const t = casas == null ? String(num) : num.toFixed(casas);
    return {texto: t + sufixo, ausente: false};
  }
  function pintaValor(el, n, opcoes) {
    if (!el) return;
    const v = valor(n, opcoes);
    el.classList.toggle('qhos-absent', v.ausente);
    const t = v.ausente ? (opcoes && opcoes.ausente) || 'sem dado' : v.texto;
    if (el.textContent !== t) el.textContent = t;
  }

  // --- 5. Tooltips acessiveis --------------------------------------------
  // Converte [data-tip] e [title] em gatilhos focaveis. Um unico balao
  // reaproveitado; ouvintes delegados; sem timer.
  let balao = null, aberto = null;
  function garanteBalao() {
    if (balao && balao.isConnected) return balao;
    balao = document.createElement('div');
    balao.className = 'qhos-tip-bubble';
    balao.id = 'qhos-tip-bubble';
    balao.setAttribute('role', 'tooltip');
    balao.hidden = true;
    document.body.appendChild(balao);
    return balao;
  }
  function posiciona(gatilho) {
    const b = garanteBalao();
    const r = gatilho.getBoundingClientRect();
    b.hidden = false;
    const bb = b.getBoundingClientRect();
    let left = r.left + r.width / 2 - bb.width / 2;
    left = Math.max(12, Math.min(left, window.innerWidth - bb.width - 12));
    let top = r.top - bb.height - 8;
    if (top < 8) top = r.bottom + 8;                 // vira para baixo se nao couber
    b.style.left = Math.round(left) + 'px';
    b.style.top = Math.round(top) + 'px';
  }
  function abre(gatilho) {
    if (aberto === gatilho) return;
    fecha();
    const texto = gatilho.dataset.qhosTip || '';
    if (!texto) return;
    const b = garanteBalao();
    b.textContent = texto;
    gatilho.setAttribute('aria-expanded', 'true');
    gatilho.setAttribute('aria-describedby', b.id);
    aberto = gatilho;
    posiciona(gatilho);
  }
  function fecha() {
    if (!aberto) return;
    aberto.setAttribute('aria-expanded', 'false');
    aberto.removeAttribute('aria-describedby');
    aberto = null;
    if (balao) balao.hidden = true;
  }
  // Promove um elemento a gatilho acessivel, preservando o texto original.
  function promove(el) {
    if (!el || el.dataset.qhosTipReady === '1') return null;
    const texto = (el.dataset.tip || el.getAttribute('title') || '').trim();
    if (!texto) return null;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'qhos-tip-trigger';
    btn.dataset.qhosTip = texto;
    btn.dataset.qhosTipReady = '1';
    btn.setAttribute('aria-expanded', 'false');
    btn.setAttribute('aria-label', 'Explicacao: ' + texto);
    const marca = document.createElement('span');
    marca.setAttribute('aria-hidden', 'true');
    marca.textContent = (el.textContent || '?').trim() || '?';
    btn.appendChild(marca);
    el.replaceWith(btn);
    return btn;
  }
  // Para controles que ja sao interativos: mantem o elemento, so troca o
  // title (que some no toque e no teclado) por um tooltip de verdade.
  function anexa(el) {
    if (!el || el.dataset.qhosTipReady === '1') return el;
    const texto = (el.dataset.tip || el.getAttribute('title') || '').trim();
    if (!texto) return el;
    el.dataset.qhosTip = texto;
    el.dataset.qhosTipReady = '1';
    el.removeAttribute('title');
    if (!el.getAttribute('aria-label') && !el.textContent.trim()) {
      el.setAttribute('aria-label', texto);
    }
    return el;
  }
  function melhoraTooltips(raiz = document) {
    // '?' decorativos viram botoes; controles reais so trocam o title.
    raiz.querySelectorAll('span[data-tip]:not([data-qhos-tip-ready])').forEach(promove);
    raiz.querySelectorAll(
      'button[title]:not([data-qhos-tip-ready]),a[title]:not([data-qhos-tip-ready]),'
      + '[role=button][title]:not([data-qhos-tip-ready]),summary[title]:not([data-qhos-tip-ready]),'
      + '[data-qhos-tip-attach]:not([data-qhos-tip-ready])'
    ).forEach(anexa);
  }
  function aoEntrar(e) {
    const g = e.target.closest?.('[data-qhos-tip]');
    if (g) abre(g); else if (aberto && !e.target.closest?.('.qhos-tip-bubble')) fecha();
  }
  function aoFoco(e) { const g = e.target.closest?.('[data-qhos-tip]'); if (g) abre(g); }
  function aoBorrar() { fecha(); }
  function aoTecla(e) { if (e.key === 'Escape') fecha(); }
  function aoClique(e) {
    const g = e.target.closest?.('[data-qhos-tip]');
    if (g && g.classList.contains('qhos-tip-trigger')) {
      e.preventDefault();
      if (aberto === g) fecha(); else abre(g);
      return;
    }
    if (!e.target.closest?.('.qhos-tip-bubble')) fecha();
  }
  document.addEventListener('pointerover', aoEntrar, true);
  document.addEventListener('focusin', aoFoco, true);
  document.addEventListener('focusout', aoBorrar, true);
  document.addEventListener('keydown', aoTecla, true);
  document.addEventListener('click', aoClique, true);
  window.addEventListener('scroll', fecha, true);
  window.addEventListener('resize', fecha);

  // --- 6. Guarda de movimento --------------------------------------------
  // Escreve data-qhos-motion no <html>. A folha comum usa esse atributo para
  // desligar animacao infinita. Sem timer: so ouve a media query e o evento
  // ja emitido pelo helper de observacao.
  const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  let pausadoManual = false;
  try { pausadoManual = localStorage.getItem('qhos-motion-paused') === 'true'; } catch {}
  function movimentoDesligado() { return pausadoManual || !!mq?.matches; }
  function aplicaMovimento() {
    document.documentElement.dataset.qhosMotion = movimentoDesligado() ? 'off' : 'on';
  }
  mq?.addEventListener?.('change', aplicaMovimento);
  window.addEventListener('qhos-motion', (e) => {
    if (e.detail && typeof e.detail.paused === 'boolean') pausadoManual = e.detail.paused;
    aplicaMovimento();
  });
  aplicaMovimento();

  // --- 7. Atividade observada --------------------------------------------
  // Unico caminho autorizado para ligar animacao de atividade: exige uma
  // janela fechada, recente e com contagem positiva. Sem isso, devolve false
  // e a interface fica parada.
  function atividadeComprovada(janela, agora = Date.now(), limiteMs = 5000) {
    if (!janela) return false;
    const ini = nsParaMs(janela.start_ns), fim = nsParaMs(janela.end_ns);
    if (!Number.isFinite(ini) || !Number.isFinite(fim)) return false;
    if (fim <= ini || fim - ini > limiteMs) return false;
    if (agora - fim < 0 || agora - fim > limiteMs) return false;
    let n = 0;
    try { n = Number(BigInt(janela.count)); } catch { return false; }
    return n > 0;
  }
  // Liga/desliga a animacao de um elemento a partir da evidencia acima.
  function marcaAtividade(el, janela, agora) {
    if (!el) return false;
    const ativo = !movimentoDesligado() && atividadeComprovada(janela, agora);
    el.dataset.qhosActive = String(ativo);
    return ativo;
  }

  function dispose() {
    document.removeEventListener('pointerover', aoEntrar, true);
    document.removeEventListener('focusin', aoFoco, true);
    document.removeEventListener('focusout', aoBorrar, true);
    document.removeEventListener('keydown', aoTecla, true);
    document.removeEventListener('click', aoClique, true);
    window.removeEventListener('scroll', fecha, true);
    window.removeEventListener('resize', fecha);
    mq?.removeEventListener?.('change', aplicaMovimento);
    fecha();
    balao?.remove(); balao = null;
  }
  window.addEventListener('pagehide', dispose, {once: true});

  /* --- 8. idade do dado no componente de observacao ----------------------
     O helper canonico ja mostra "Sessao atual" ou "Captura historica", mas a
     linha de contexto nao dizia HA QUANTO TEMPO o ultimo evento chegou: num
     painel podia ler-se "Sessao atual" ao lado de um BBO de horas atras.
     O chip abaixo e acrescentado por fora, sem tocar no helper: ele reage ao
     evento qhos-rithmic-visual que o proprio helper ja emite a cada pintura,
     e por isso nao cria timer nem requisicao. */
  function chipIdade(m) {
    if (!m) return;
    for (const raiz of document.querySelectorAll('.rithmic-observation')) {
      const ctx = raiz.querySelector('.ro-context');
      if (!ctx) continue;
      let chip = ctx.querySelector('.qhos-ro-age');
      if (!chip) {
        chip = document.createElement('span');
        chip.className = 'qhos-ro-age';
        ctx.appendChild(chip);
      }
      const s = m.op || m.summary || {};
      const ms = nsParaMs(s.last_update && s.last_update.received_ns);
      if (!Number.isFinite(ms)) {
        chip.dataset.qhosFresh = 'unknown';
        chip.textContent = 'idade do dado não informada';
        chip.title = 'O contrato nao trouxe last_update.received_ns nesta leitura.';
        continue;
      }
      const prazo = Number.isFinite(s.freshness_ms) ? s.freshness_ms : 12000;
      const i = idade(ms, Date.now(), prazo);
      chip.dataset.qhosFresh = i.fresh;
      chip.textContent = 'último evento há ' + i.texto + ' · ' + utcExato(ms);
      chip.title = i.fresh === 'no'
        ? 'A ultima observacao passou do prazo de frescor declarado ('
          + Math.round(prazo / 1000) + ' s).'
        : 'Recepcao do ultimo evento, em UTC.';
    }
  }
  window.addEventListener('qhos-rithmic-visual', (e) => {
    try { chipIdade(e.detail); } catch (err) { console.warn('[qhos-ui] idade', err); }
  });

  window.QHOSUI = {
    STATES, ordem, normaliza, badge, motivo, valor, pintaValor,
    idade, utcExato, dataEfetiva, nsParaMs,
    melhoraTooltips, promoveTooltip: promove, anexaTooltip: anexa, fechaTooltip: fecha,
    chipIdade,
    movimentoDesligado, atividadeComprovada, marcaAtividade, dispose,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => melhoraTooltips(), {once: true});
  } else {
    melhoraTooltips();
  }
})();
