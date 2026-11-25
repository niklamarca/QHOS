"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let state = null, selected = 'brain', detail = 'activity', busyChat = false, speak = false, pollBusy = false, regressionBusy = false, telemetryFresh = false, preferredVoice = null, neuralAudio = null, neuralVoiceAbort = null, neuralVoiceRequest = 0;
  // The neural volume is a live visual identity and remains in motion while
  // the page is visible. The user can still drag it to inspect another angle.
  let activityUntil=0, previousCycles=null, rithmicVisual=null;
  const activeRequests=new Set();
  let mode = 'neural', rotation = .3, tilt = -.12, dragging = false;
  let lastPointer = null, toastTimer, activeCapability = 'brain', voiceRecognition = null, immersiveResult = false, linuxBusy = false;
  let sceneZoom = 1, targetSceneZoom = 1;
  const capabilities = {
    brain:{title:'Cognição',summary:'Auditar, investigar e aprender. Cada incidente preserva sua história, estratégia e evidências.',actions:[
      ['brain-mission','Missão do Jarvis','Auditoria do servidor, pesquisa e assistência pessoal.'],
      ['brain-memories','Memórias e estratégias','Incidentes salvos, próximos testes e resultados comprovados.'],
      ['brain-linux','Auditar o Linux','Consultar recursos e serviços reais do host do Brain.'],
      ['brain-featured','Em foco agora','Uma memória relevante do servidor, sem perder o histórico.']
    ]},
    trader:{title:'Trader',summary:'Estado operacional observado sem acoplar o ciclo de vida do Trader ao Brain.',actions:[
      ['trader-status','Estado operacional agora','Conexão, integridade, geração e prontidão de execução.'],
      ['trader-paper','Experimento paper ES','Resultados brutos das hipóteses de tendência e reversão; sessão de 10/09.'],
      ['trader-quotes','Rithmic · cotações e gravação','Amostras de preços, spreads, continuidade e histórico de pesquisa.'],
      ['trader-protocol','Rithmic API · observações','Capturas identificadas, negócios, BBO e limites da observação.'],
      ['trader-modules','Módulos observados','Saúde individual dos componentes reportados.'],
      ['trader-refresh','Atualizar telemetria','Fazer uma nova leitura sem executar ações no mercado.'],
      ['trader-restart','Reinício protegido','Abrir a confirmação e validar todas as guardas antes de religar.']
    ]},
    tester:{title:'Diagnóstico',summary:'Checks rápidos, regressão operacional e auditoria ampla permanecem separados.',actions:[
      ['tester-quick','Executar verificações rápidas','Diagnóstico operacional limitado; não substitui regressão.'],
      ['tester-regression','Executar regressão','Solicitar a suíte operacional isolada.'],
      ['tester-audit','Ver auditoria ampla','Consultar evidências externas sem inventar aprovação.'],
      ['tester-history','Histórico de verificações','Resultados preservados das execuções anteriores.']
    ]},
    watchdog:{title:'Proteção',summary:'Incidentes, limites de autonomia e mecanismos de recuperação protegida.',actions:[
      ['guard-incidents','Incidentes atuais','Ocorrências que precisam de atenção.'],
      ['guard-watchdog','Estado da proteção','Watchdog, fase e limites observados.'],
      ['guard-authority','Autoridade do Brain','Autonomia, shadow mode e controles expostos.'],
      ['guard-boundaries','Limites operacionais','O que o Brain pode observar, sugerir ou solicitar.']
    ]}
  };
  const pretty = v => typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v ?? 'Não disponível');
  const stamp = v => v ? new Date(typeof v === 'number' ? v * 1000 : v).toLocaleTimeString('pt-BR') : '—';
  const ageLabel = seconds => !Number.isFinite(seconds) ? 'IDADE INDISPONÍVEL' : seconds < 60 ? 'HÁ MENOS DE 1 MIN' : seconds < 3600 ? `HÁ ${Math.floor(seconds/60)} MIN` : seconds < 86400 ? `HÁ ${Math.floor(seconds/3600)} H` : `HÁ ${Math.floor(seconds/86400)} DIAS`;
  const countLabel = value => Number.isFinite(value) && value >= 0 ? String(value) : '—';
  const cadenceLabel = value => Number.isFinite(value) && value > 0 ? value === 3600 ? '1 hora' : `${Math.round(value/60)} min` : 'cadência indisponível';
  const normalizeVoice=text=>String(text||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  const dateLabel = value => {
    if(!value)return 'Não disponível';
    const date=new Date(typeof value==='number'?value*1000:value);
    return Number.isFinite(date.getTime())?date.toLocaleString('pt-BR'):'Não disponível';
  };
  const modelLabel = assistant => `${assistant?.model_requested || 'Modelo não informado'} · ${assistant?.model_status==='connected' && assistant?.generative_model===true ? 'conectado' : `não confirmado (${assistant?.model_status || 'sem estado'}) · fallback local`}`;
  const feminineVoiceHints = ['francisca','fernanda','camila','luciana','maria','vitoria','vitória','leticia','letícia','joana','helena','zira','female'];
  function selectNaturalVoice(){
    const synth=window.speechSynthesis;if(!synth?.getVoices)return null;
    const voices=synth.getVoices();
    preferredVoice=voices.slice().sort((left,right)=>voiceScore(right)-voiceScore(left))[0]||null;
    return preferredVoice;
  }
  function voiceScore(voice){
    const name=normalizeVoice(voice?.name),lang=String(voice?.lang||'').toLowerCase();let score=0;
    if(lang==='pt-br')score+=120;else if(lang.startsWith('pt'))score+=70;else score-=80;
    if(/natural|neural|online/.test(name))score+=45;
    if(feminineVoiceHints.some(hint=>name.includes(normalizeVoice(hint))))score+=30;
    if(name.includes('francisca'))score+=25;if(name.includes('maria'))score+=15;if(voice?.localService===false)score+=8;
    if(/daniel|male/.test(name))score-=35;
    return score;
  }
  function speechChunks(text){
    const sentences=String(text||'').replace(/\s+/g,' ').trim().match(/[^.!?;:]+[.!?;:]?|.+$/g)||[];const chunks=[];
    for(const sentence of sentences){if(sentence.length<=230){chunks.push(sentence.trim());continue;}for(let at=0;at<sentence.length;at+=220)chunks.push(sentence.slice(at,at+220).trim());}
    return chunks.filter(Boolean).slice(0,24);
  }
  function speakNaturally(text,{interrupt=true}={}){
    const synth=window.speechSynthesis;if(!synth||typeof window.SpeechSynthesisUtterance!=='function')return false;
    const voice=selectNaturalVoice();if(interrupt)synth.cancel();
    for(const chunk of speechChunks(text)){const utterance=new SpeechSynthesisUtterance(chunk);utterance.lang=voice?.lang||'pt-BR';if(voice)utterance.voice=voice;utterance.rate=.94;utterance.pitch=1.04;utterance.volume=1;synth.speak(utterance);}
    return true;
  }
  function voiceExcerpt(text){
    const clean=String(text||'')
      .replace(/```[\s\S]*?```/g,' Trecho de código disponível no chat. ')
      .replace(/\[([^\]]+)\]\(https?:\/\/[^\s)]+\)/g,'$1')
      .replace(/https?:\/\/\S+/g,'')
      .replace(/^\s*(?:[-*#>]\s+|\d+[.)]\s+)/gm,'')
      .replace(/[*_`#]/g,'').replace(/\s+/g,' ').trim();
    if(!clean)return '';
    if(clean.length<=1600)return clean;
    const prefix=clean.slice(0,1550);
    const sentenceEnd=Math.max(prefix.lastIndexOf('. '),prefix.lastIndexOf('! '),prefix.lastIndexOf('? '));
    const excerpt=sentenceEnd>1100?prefix.slice(0,sentenceEnd+1):prefix.replace(/\s+\S*$/,'')+'…';
    return `${excerpt} A resposta completa está no chat.`;
  }
  function stopNeuralVoice(){
    neuralVoiceRequest+=1;
    neuralVoiceAbort?.abort();neuralVoiceAbort=null;
    if(neuralAudio){neuralAudio.pause();URL.revokeObjectURL(neuralAudio.src);neuralAudio=null;}
  }
  async function speakReply(text){
    if(!speak)return false;
    const excerpt=voiceExcerpt(text);if(!excerpt)return false;
    const requestId=++neuralVoiceRequest;
    neuralVoiceAbort?.abort();
    const controller=new AbortController();neuralVoiceAbort=controller;
    try{
      const response=await fetch('/api/voice/synthesize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:excerpt}),signal:controller.signal});
      if(!response.ok)throw new Error('voice_service_unavailable');
      const source=URL.createObjectURL(await response.blob());
      if(!speak||requestId!==neuralVoiceRequest){URL.revokeObjectURL(source);return false;}
      const audio=new Audio(source);audio.preload='auto';audio.playsInline=true;
      if(neuralAudio){neuralAudio.pause();URL.revokeObjectURL(neuralAudio.src);}
      neuralAudio=audio;audio.onended=()=>{URL.revokeObjectURL(audio.src);if(neuralAudio===audio)neuralAudio=null;};
      await audio.play();voiceFeedback('Voz neural local ativa.');return true;
    }catch(error){
      if(error?.name==='AbortError'||requestId!==neuralVoiceRequest)return false;
      voiceFeedback('Voz neural indisponível. Usando a voz do dispositivo.');
      return speakNaturally(excerpt,{interrupt:true});
    }finally{if(neuralVoiceAbort===controller)neuralVoiceAbort=null;}
  }
  window.speechSynthesis?.addEventListener?.('voiceschanged',selectNaturalVoice);selectNaturalVoice();
  const verificationLabel = regression => regression?.verified===true || regression?.verification?.verified===true ? 'Evidências verificadas' : 'Aprovação não comprovada';
  function regressionLabel(regression) {
    if(!regression)return 'Regressão operacional indisponível · não é a auditoria ampla';
    if(['NOT_RUN','NOT_CONFIGURED'].includes(regression.phase))return `${regression.phase} · sem execução comprovada`;
    return `${regression.phase || 'INDETERMINADO'} · ${countLabel(regression.passed)} aprovados · ${countLabel(regression.failed)} falhas · ${countLabel(regression.collection_errors)} erros de coleta · ${verificationLabel(regression)}${regression.stale?' · DESATUALIZADA':''}`;
  }
  function auditLabel(audit) {
    if(!audit?.available)return 'Relatório amplo indisponível · checks rápidos não comprovam a regressão';
    if(audit.phase==='EMPTY')return 'Auditoria ampla sem testes executados · não comprova aprovação';
    return `${audit.failed} falhas · ${audit.collection_errors} erros de coleta · ${audit.passed} aprovados${audit.stale?' · DESATUALIZADA':''}`;
  }
  function put(id, text) { if ($(id)) $(id).textContent = text ?? '—'; }
  function toast(text) { put('toast', text); $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 6000); }
  async function request(url, body) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), body && url.includes('relight') ? 240000 : url.includes('/chat') ? 135000 : url==='/api/assistant/state' ? 8000 : 30000);activeRequests.add(controller);
    try {
      const r = await fetch(url, {signal: controller.signal, ...(body ? {method:'POST',headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {})});
      const result = await r.json(); if (!r.ok) throw new Error(result.reason || `HTTP ${r.status}`); return result;
    } finally { clearTimeout(timeout);activeRequests.delete(controller); }
  }
  function metric(name, value, info, good) {
    put(name+'-status', value); put(name+'-detail', info);
    $(name+'-status').style.color = good ? '#65ffab' : '#d6c484';
    $(name+'-bar').style.width = good ? '100%' : '25%';
    put('node-'+name+'-status', value);
  }
  async function poll() {
    if (pollBusy || document.hidden) return; pollBusy = true;
    try {
      state = await request('/api/assistant/state');
      if(document.hidden)return;
      telemetryFresh = true;
      const b=state.brain,t=state.trader,w=state.watchdog,test=state.tester;
      const v2=t.qhos2_observation||{}, capture=v2.capture||{}, quality=v2.quality||{};
      put('qhos2-observation-state',v2.available ? `${v2.environment} · ${capture.exchange||'—'}/${capture.symbol||'—'} · ${capture.closed?'Captura encerrada':'Arquivo em acompanhamento'}` : 'Observação V2 indisponível ou desatualizada');
      put('qhos2-observation-detail',v2.available ? `Livro ${quality.book_valid?'validado pelo leitor':'não qualificado'} · ${capture.frames??'—'} quadros. Sem confirmação de sessão ao vivo. Negociação desabilitada.` : 'Sem evidência atual para qualificar dados ou sessão. Negociação desabilitada.');
      window.QHOSRithmicObservation?.render(t.rithmic_protocol_observation);
      if(b.running&&Number.isFinite(b.cycles)&&previousCycles!==null&&b.cycles>previousCycles)activityUntil=performance.now()+2200;
      if(test.phase==='RUNNING')activityUntil=performance.now()+4000;
      previousCycles=Number.isFinite(b.cycles)?b.cycles:null;
      const traderUncertain=t.connection_state==='INDETERMINATE'||t.telemetry_partial||t.stale;
      metric('brain', b.running ? 'ATIVO':'OFFLINE', `${b.cycles ?? 0} ciclos de observação`, b.running);
      metric('trader', traderUncertain ? 'INDETERMINADO' : t.reachable ? 'ONLINE':'OFFLINE', `${traderUncertain ? (t.stale?'Telemetria desatualizada':'Telemetria parcial · health não confirmou') : `Processo ${t.process}`} · geração ${t.generation ?? '—'}`, t.reachable&&!traderUncertain);
      $('node-trader-status').title=traderUncertain?'Saúde indeterminada; a telemetria disponível não confirma prontidão.':t.reachable?'Processo confirmado pelo health.':'Nenhum sinal operacional confirmado nesta observação.';
      metric('tester', test.display_phase||test.phase, `${test.passed} aprovados · ${test.failed} falhas · checks rápidos${test.stale?' · DESATUALIZADO':''}`, test.phase==='PASSED'&&!test.stale);
      put('audit-summary', auditLabel(state.regression_audit));
      put('audit-age', ageLabel(state.regression_audit?.age_seconds));
      $('audit-button').classList.toggle('warn', !state.regression_audit?.available || state.regression_audit.stale || state.regression_audit.phase==='EMPTY' || state.regression_audit.failed>0 || state.regression_audit.collection_errors>0);
      put('regression-title',`Regressão operacional · ${cadenceLabel(state.regression?.cadence_s)}`);
      put('regression-summary',regressionLabel(state.regression));
      put('regression-next',state.regression?.next_due ? `PRÓXIMA: ${dateLabel(state.regression.next_due)}` : 'SEM PRÓXIMA DATA');
      $('regression-button').classList.toggle('warn',state.regression?.verified!==true || state.regression?.stale || state.regression?.failed>0 || state.regression?.collection_errors>0);
      metric('watchdog', w.enabled ? 'VIGIANDO':'DESLIGADO', w.phase ?? 'Aguardando', w.enabled);
      put('connection-label','Núcleo conectado'); $('connection-led').style.background='#65ffab';
      put('core-state', traderUncertain ? 'TELEMETRIA PARCIAL · ESTADO INDETERMINADO' : test.phase==='RUNNING' ? 'DIAGNÓSTICO EM CURSO' : t.reachable ? 'SISTEMAS CONECTADOS' : 'ALERTA · TRADER OFFLINE');
      put('core-state-detail', `Brain independente · ${b.cycles ?? 0} ciclos reais`);
      put('core-footer-status', `Observação ${stamp(t.observed_at)} · ${Object.keys(t.modules||{}).length} módulos`);
      put('workspace-path', state.target_root); put('test-count',test.cases?.length ?? 0); put('incident-count',state.incidents?.length ?? 0);
      put('last-updated',stamp(t.observed_at));
      put('assistant-presence',modelLabel(state.assistant));
      refreshMemoryFocus();
      renderDetail();
    } catch(e) {
      telemetryFresh = false;activityUntil=0;window.QHOSRithmicObservation?.unavailable();
      for(const name of ['brain','trader','tester','watchdog']){metric(name,'DADOS ANTIGOS','Última leitura · conexão indisponível',false);document.querySelector('[data-pick='+name+']')?.setAttribute('data-work','unknown');}
      put('connection-label','Brain sem conexão'); $('connection-led').style.background='#e7b06b';
      put('core-state','CONEXÃO INTERROMPIDA'); put('core-state-detail','Última leitura preservada · não é estado atual');
      put('last-updated','LEITURA DESATUALIZADA');
      put('audit-age','CONEXÃO INDISPONÍVEL');
      put('regression-next','LEITURA DESATUALIZADA');
      put('assistant-presence','Conexão indisponível · estado do modelo não confirmado');
      if(detail==='regression'||detail==='cognition')renderDetail();
    } finally { pollBusy=false; }
  }
  function setDetail(next) { detail=next; document.querySelectorAll('[data-detail]').forEach(el=>{el.classList.toggle('selected',el.dataset.detail===detail);el.setAttribute('aria-selected',String(el.dataset.detail===detail));});renderDetail(); }
  const coreExpanded = () => document.fullscreenElement === $('core-panel') || Boolean($('core-panel').classList.contains?.('expanded'));
  function selectSystem(key) { selected=key;put('inspector-badge',key.toUpperCase());document.querySelectorAll('[data-pick]').forEach(el=>el.classList.toggle('selected',el.dataset.pick===key)); }
  function pick(key) { selectSystem(key);if(coreExpanded()&&capabilities[key])enterCapability(key);else setDetail('inspector'); }
  function exitImmersive(){
    immersiveResult=false;targetSceneZoom=1;$('immersive-layer').hidden=true;$('brain-stage').classList.remove('immersive-active','immersive-result-active');
  }
  function renderImmersiveMenu(){
    const capability=capabilities[activeCapability]||capabilities.brain;
    immersiveResult=false;targetSceneZoom=2.35;$('brain-stage').classList.remove('immersive-result-active');
    put('immersive-path',`BRAIN / ${capability.title.toUpperCase()}`);$('immersive-back').lastElementChild.textContent='VOLTAR AO NÚCLEO';
    $('immersive-content').innerHTML=`<div class="immersive-system-intro"><span class="immersive-index">SISTEMA 0${Object.keys(capabilities).indexOf(activeCapability)+1}</span><div class="immersive-orb"><svg><use href="#${activeCapability==='tester'?'i-test':activeCapability==='watchdog'?'i-shield':activeCapability==='trader'?'i-server':'i-brain'}"/></svg></div><p class="eyebrow">ACESSO NEURAL</p><h2>${escape(capability.title)}</h2><p>${escape(capability.summary)}</p></div><div class="immersive-actions">${capability.actions.map(([id,label,description],index)=>`<button class="immersive-action action-${index+1}" data-capability-action="${escape(id)}" style="--action-index:${index}"><span class="action-number">0${index+1}</span><span class="action-copy"><b>${escape(label)}</b><small>${escape(description)}</small></span><i aria-hidden="true">↗</i></button>`).join('')}</div>`;
    $('immersive-content').querySelectorAll('[data-capability-action]').forEach(button=>button.onclick=()=>executeCapability(button.dataset.capabilityAction));
    refreshMemoryFocus();
  }
  function memoryCards(){return state?.cognition?.memory_cards||[];}
  function refreshMemoryFocus(){
    if(activeCapability!=='brain'||immersiveResult||$('immersive-layer').hidden)return;
    const button=document.querySelector('[data-capability-action="brain-featured"]');
    if(!button||button.matches(':hover')||button.contains(document.activeElement))return;
    const cards=memoryCards(),card=cards.length?cards[Math.floor(Date.now()/45000)%Math.min(cards.length,6)]:null;
    button.dataset.memoryId=card?.id||'';
    button.querySelector('b').textContent=card?card.title:'Em foco agora';
    button.querySelector('small').textContent=card?`${card.label} · ${card.occurrences} ocorrência(s) · abrir memória`:'Novos incidentes aparecerão aqui quando houver evidências.';
  }
  function memoryValue(value){
    try{const parsed=JSON.parse(value);if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed))return `<dl class="memory-facts">${Object.entries(parsed).map(([key,val])=>`<div><dt>${escape(key.replaceAll('_',' '))}</dt><dd>${escape(typeof val==='object'?pretty(val):val)}</dd></div>`).join('')}</dl>`;}catch{}
    return `<p>${escape(value)}</p>`;
  }
  function showCognitiveRecords(filter='all'){
    showImmersiveResult('Memórias e estratégias',{},'Registros persistentes do Brain. Os destaques mudam; o histórico permanece.');
    const result=$('immersive-content').querySelector('article');result.classList.add('memory-view');result.querySelector('pre').remove();
    const cards=memoryCards().filter(card=>filter==='all'||(filter==='resolved'?card.verified_repair:card.phase===filter));
    result.insertAdjacentHTML('beforeend',`<div class="memory-filters" role="group" aria-label="Filtrar memórias">${[['all','Todas'],['investigating','Em investigação'],['resolved','Correções'],['verified','Verificações'],['historical','Históricas']].map(([key,label])=>`<button data-memory-filter="${key}" aria-pressed="${filter===key}">${label}</button>`).join('')}</div><div class="memory-grid">${cards.map(card=>`<button class="memory-card phase-${escape(card.phase)}" data-memory-id="${escape(card.id)}"><span class="memory-label">${escape(card.label)}</span><h3>${escape(card.title)}</h3><p>${escape(card.problem)}</p><footer><span>${escape(dateLabel(card.updated_at))}</span><span>${escape(card.occurrences)}× ↗</span></footer></button>`).join('')||'<p class="memory-empty">Nenhum registro nesta categoria. O Brain não cria memórias fictícias para preencher a tela.</p>'}</div>`);
    result.querySelectorAll('[data-memory-filter]').forEach(button=>button.onclick=()=>showCognitiveRecords(button.dataset.memoryFilter));
    result.querySelectorAll('[data-memory-id]').forEach(button=>button.onclick=()=>openMemory(button.dataset.memoryId));
  }
  function openMemory(id){
    const card=memoryCards().find(row=>row.id===id);if(!card){showCognitiveRecords();return;}
    showImmersiveResult(card.title,{},'Memória histórica · não substitui uma nova verificação do servidor.');
    const result=$('immersive-content').querySelector('article');result.classList.add('memory-view');result.querySelector('pre').remove();
    result.insertAdjacentHTML('beforeend',`<span class="memory-label phase-${escape(card.phase)}">${escape(card.label)}</span><div class="memory-story"><section><span>01 / OCORRÊNCIA</span><h3>O que aconteceu</h3>${memoryValue(card.problem)}</section><section><span>02 / ESTRATÉGIA</span><h3>Como investigar ou corrigir</h3>${memoryValue(card.strategy)}</section><section><span>03 / RESULTADO</span><h3>O que foi comprovado</h3>${memoryValue(card.result)}</section></div><section class="memory-sources"><h3>Fontes preservadas</h3>${card.sources.map(source=>`<p>${escape(source)}</p>`).join('')}<small>${escape(dateLabel(card.updated_at))} · ${escape(card.occurrences)} ocorrência(s)</small></section><button class="button subdued" id="memory-all">Todas as memórias</button>`);
    $('memory-all').onclick=()=>showCognitiveRecords();
  }
  function showJarvisMission(){
    showImmersiveResult('Jarvis, por você',{},state.assistant?.mission||'Auditar o servidor e ajudar no seu dia a dia.');
    const result=$('immersive-content').querySelector('article');result.classList.add('memory-view');result.querySelector('pre').remove();
    result.insertAdjacentHTML('beforeend',`<div class="memory-story"><section><span>01 / INVESTIGAR</span><h3>Servidor em primeiro lugar</h3><p>Consultar telemetria, auditar o Linux, correlacionar logs e preservar evidências de incidentes.</p></section><section><span>02 / PESQUISAR</span><h3>Resultados com fontes</h3><p>Pesquisar seus pedidos na internet e consultar dados autorizados. Quando faltar conexão ou acesso técnico, informar exatamente o que falta.</p></section><section><span>03 / CORRIGIR</span><h3>Automação com prova</h3><p>Detecção, investigação e registro automáticos. Aplicar correções no Trader ainda depende de um executor protegido, testes e validação; não está ativado.</p></section></div><div class="memory-sources"><h3>${escape(modelLabel(state.assistant))}</h3><p>Uma estratégia proposta não é uma correção executada. O Jarvis deve mostrar essa diferença.</p></div>`);
  }
  function enterCapability(key='brain'){
    if(!coreExpanded()){pick(key);return;}
    activeCapability=capabilities[key]?key:'brain';selectSystem(activeCapability);$('immersive-layer').hidden=false;$('brain-stage').classList.add('immersive-active');
    renderImmersiveMenu();
  }
  function showImmersiveResult(title,value,description='Leitura atual do Brain'){
    immersiveResult=true;targetSceneZoom=3.15;$('brain-stage').classList.add('immersive-result-active');put('immersive-path',`BRAIN / ${capabilities[activeCapability]?.title.toUpperCase() || 'AÇÕES'} / LEITURA`);$('immersive-back').lastElementChild.textContent='VOLTAR ÀS AÇÕES';
    const box=$('immersive-content');box.replaceChildren();const result=document.createElement('article');result.className='immersive-result';const kicker=document.createElement('span');kicker.className='immersive-index';kicker.textContent='LEITURA ESTRUTURADA';const heading=document.createElement('h2');heading.textContent=title;const summary=document.createElement('p');summary.textContent=description;const pre=document.createElement('pre');pre.textContent=pretty(value);result.append(kicker,heading,summary,pre);box.append(result);
  }
  async function executeCapability(action){
    if(!state&&action!=='trader-refresh'){toast('Aguardando a primeira leitura do Brain.');return;}
    if(action==='brain-mission')showJarvisMission();
    else if(action==='brain-memories')showCognitiveRecords();
    else if(action==='brain-featured')openMemory(document.querySelector('[data-capability-action="brain-featured"]')?.dataset.memoryId);
    else if(action==='brain-linux'){const response=await chat('Audite agora o Linux que hospeda o Brain. Consulte recursos e serviços e apresente evidências, sem reiniciar nada.');if(response)showImmersiveResult('Auditoria do Linux',response.reply,'Consulta real · nenhuma alteração solicitada.');}
    else if(action==='brain-overview')showImmersiveResult('Visão da inteligência',state.cognition||{},'Conhecimento, memória e limites observados.');
    else if(action==='brain-investigations')showImmersiveResult('Investigações recentes',{incidentes:state.cognition?.automatic_incidents||[],rastros:state.cognition?.recent_traces||state.cognition?.traces||[]},'Logs completos, fontes consultadas e hipóteses. Uma investigação aberta não significa causa confirmada.');
    else if(action==='brain-model')showImmersiveResult('Modelo e resposta',state.assistant||{},modelLabel(state.assistant));
    else if(action==='brain-ask'){const response=await chat('Explique o estado atual do Brain e o que precisa da minha atenção.');if(response)showImmersiveResult('Resposta do Brain',response.reply||response,'Pergunta enviada ao Jarvis e registrada também no chat.');}
    else if(action==='trader-status')showImmersiveResult('Estado operacional agora',state.trader||{},'Processo, integridade e autorização são estados distintos.');
    else if(action==='trader-paper'){const s=state.trader?.sampled_market_quotes?.paper_study||{};showImmersiveResult('Experimento paper ES',{sessão:s.plan?.session,estado:s.phase,barras_de_aquecimento:s.warmup_bars,interrupções:s.gaps,hipóteses:s.arms,resultado_líquido:'Indisponível: corretagem não informada'},'Simulação local experimental. NAV bruto inclui spread e 1 tick hipotético de deslizamento. Não representa fills da corretora ou estratégia validada.');}
    else if(action==='trader-quotes')showImmersiveResult('Rithmic · coleta para pesquisa',state.trader?.sampled_market_quotes||{},'Cotações amostradas a cada segundo. Sem MBO, identificação do agressor ou fills reais.');
    else if(action==='trader-protocol'){const s=state.trader?.rithmic_protocol_observation||{};showImmersiveResult('Rithmic API · observação de pesquisa',{origem:s.source_kind,ambiente:s.environment,instrumento:s.instrument,período:s.last_update,captura:s.capture,negócios:s.trades,BBO:s.bbo,limites:s.quality,hipóteses:'Nenhuma produzida por esta visualização',research_only:true,can_execute:false},'Dados identificados na captura. Test não demonstra oportunidade econômica; BBO não estabelece MBO.');}
    else if(action==='trader-modules')showImmersiveResult('Módulos observados',state.trader?.modules||{},'Telemetria reportada pelo Trader nesta leitura.');
    else if(action==='trader-refresh'){await poll();if(state)showImmersiveResult(telemetryFresh?'Telemetria atualizada':'Última telemetria · atualização indisponível',state.trader||{},`Última observação: ${dateLabel(state.trader?.observed_at)}`);}
    else if(action==='trader-restart')restart();
    else if(action==='tester-quick'){await runTests();showImmersiveResult('Verificações rápidas',state?.tester||{},'Solicitação limitada ao diagnóstico operacional.');}
    else if(action==='tester-regression'){await runRegression();showImmersiveResult('Regressão operacional',state?.regression||{},'Regressão isolada; não equivale à auditoria ampla.');}
    else if(action==='tester-audit')showImmersiveResult('Auditoria ampla',state.regression_audit||{},auditLabel(state.regression_audit));
    else if(action==='tester-history')showImmersiveResult('Histórico de verificações',{rapidas:state.tester?.history||[],regressao:state.regression?.history||[]},'Resultados anteriores preservados pelo serviço.');
    else if(action==='guard-incidents')showImmersiveResult('Incidentes atuais',state.incidents||[],'Ocorrências observadas; ausência de registros não prova ausência de problemas.');
    else if(action==='guard-watchdog')showImmersiveResult('Estado da proteção',state.watchdog||{},'A recuperação automática respeita identidade, cooldown e orçamento.');
    else if(action==='guard-authority'){try{showImmersiveResult('Autoridade do Brain',await request('/api/brain/authority'),'Contrato de autonomia exposto pelo serviço.');}catch(e){toast('Autoridade indisponível: '+e.message);}}
    else if(action==='guard-boundaries')showImmersiveResult('Limites operacionais',{autonomia:'Level 2 / shadow',pode_observar:true,pode_diagnosticar:true,pode_sugerir:true,pode_executar_no_mercado:false,reinicio_do_trader:'somente com confirmação e guardas',level3:false},'O Brain não recebe autorização de execução pelo painel.');
  }
  function rows(list) { return list.length ? list.map((r,i)=>`<button class="event-row" data-row="${i}" style="width:100%;text-align:left"><span class="event-time">${escape(stamp(r.timestamp || r.ts || r.last_run))}</span><div class="event-copy"><strong>${escape(r.name || r.title || r.type || r.phase || 'Evento')}</strong><p>${escape(r.message || r.detail || r.summary || r.status || r.reason || pretty(r))}</p></div><span>↗</span></button>`).join('') : '<div class="empty-state"><p>Nenhum registro nesta leitura. Não significa ausência de problemas não detectados.</p></div>'; }
  let visibleRows=[];
  function renderDetail() {
    if(!state)return;
    const box=$('detail-content');
    if(detail==='cognition') {
      const cognition=state.cognition||{}, knowledge=cognition.knowledge||{}, memory=cognition.memory||{};
      const cards={knowledge:{label:'Conhecimento do código',description:`${countLabel(knowledge.indexed_files)} arquivos · ${countLabel(knowledge.indexed_lines)} linhas indexadas`,data:knowledge},memory:{label:'Memória local',description:`${countLabel(memory.records)} registros · ${countLabel(memory.invalidated)} invalidados`,data:memory},model:{label:'Modelo solicitado',description:modelLabel(state.assistant),data:state.assistant||{}},boundaries:{label:'Limites da inteligência',description:'Memória e código não provam o estado atual do servidor',data:cognition}};
      const traces=Array.isArray(cognition.recent_traces)?cognition.recent_traces:Array.isArray(cognition.traces)?cognition.traces:[];
      const incidents=(cognition.automatic_incidents||[]).slice().sort((a,b)=>(b.last_seen||0)-(a.last_seen||0));
      visibleRows=[...incidents.map(row=>({...row,timestamp:row.last_seen,detail:`${row.status} · ${countLabel(row.occurrences)} registros · ${countLabel(row.evidence?.sources?.length)} fontes consultadas`})),...traces.map(row=>({...row,timestamp:row.created_at,title:`Investigação ${row.id || 'sem ID'}`,detail:`${row.stage || 'Etapa não informada'} · ${countLabel(row.source_count)} fontes consultadas`}))].slice(0,100);
      box.innerHTML=`<div class="test-summary"><p><strong>Inteligência fundamentada em evidências</strong><br>${escape(telemetryFresh?(cognition.mode||'Estado indisponível'):'LEITURA DESATUALIZADA · última evidência preservada')}<br>Busca no código e memória de investigação. Isso não significa treinamento dos pesos do modelo; memória não é prova atual.</p></div><div class="data-grid">${Object.entries(cards).map(([key,card])=>`<button class="data-item" data-cognition="${key}" style="text-align:left;overflow-wrap:anywhere"><span>${escape(card.label)}</span><b>${escape(card.description)}</b></button>`).join('')}</div><div class="inspector-title"><strong>Investigações recentes</strong></div>`+rows(visibleRows);
      box.querySelectorAll('[data-cognition]').forEach(el=>el.onclick=()=>{const card=cards[el.dataset.cognition];showInfo(card.label,pretty(card.data));});
      box.querySelectorAll('[data-row]').forEach(el=>el.onclick=()=>showInfo('Rastro de investigação',pretty(visibleRows[Number(el.dataset.row)])));
    } else if(detail==='regression') {
      const regression=state.regression||{};
      const fields={fase:regression.phase,aprovados:regression.passed,falhas:regression.failed,erros_de_coleta:regression.collection_errors,ultima_execucao:dateLabel(regression.last_run),proxima_execucao:dateLabel(regression.next_due),cadencia:cadenceLabel(regression.cadence_s),verificacao:regression.verification??{verified:regression.verified,reason:regression.reason},limite:regression.boundary};
      box.innerHTML=`<div class="test-summary" style="flex-wrap:wrap"><p><strong>Regressão operacional · ${escape(cadenceLabel(regression.cadence_s))}</strong><br>${escape(telemetryFresh?regressionLabel(state.regression):'LEITURA DESATUALIZADA · resultado anterior preservado')}<br>${escape(regression.summary||'Sem resultado disponível.')}<br>Suíte operacional isolada, separada das verificações rápidas e da auditoria ampla. Guardas Python não são uma sandbox do sistema operacional.</p><button class="button subdued" id="regression-run">Executar regressão</button><button class="button subdued" id="regression-history">Histórico</button></div><div class="data-grid">${Object.entries(fields).map(([key,value])=>`<button class="data-item" data-regression-field="${key}" style="text-align:left;overflow-wrap:anywhere"><span>${escape(key)}</span><b>${escape(pretty(value).slice(0,180))}</b></button>`).join('')}</div>`;
      box.querySelectorAll('[data-regression-field]').forEach(el=>el.onclick=()=>showInfo(el.dataset.regressionField,pretty(fields[el.dataset.regressionField])));
      $('regression-run').disabled=regressionBusy||regression.phase==='RUNNING'||!telemetryFresh;
      $('regression-run').onclick=runRegression;
      $('regression-history').onclick=()=>showInfo('Histórico de regressão operacional',pretty(regression.history??[]));
    } else if(detail==='audit') {
      const audit=state.regression_audit;
      visibleRows=(audit?.findings||[]).map(row=>({...row,timestamp:audit.generated_at,title:`${row.severity} · ${row.title}`}));
      box.innerHTML=`<div class="test-summary"><p><strong>Auditoria ampla de regressão</strong><br>${escape(auditLabel(audit))}<br>${escape(ageLabel(audit?.age_seconds))} · ${escape(audit?.summary||'Aguardando relatório externo.')}</p></div>`+rows(visibleRows);
      box.querySelectorAll('[data-row]').forEach(el=>el.onclick=()=>showInfo('Achado da auditoria',pretty(visibleRows[Number(el.dataset.row)])));
    } else if(detail==='inspector') {
      const data=selected==='target'?{pasta:state.target_root,assistente:state.assistant}:state[selected]||{};
      box.innerHTML=`<div class="inspector-title"><strong>${escape(selected.toUpperCase())}</strong></div><div class="data-grid">${Object.entries(data).map(([k,v])=>`<button class="data-item" data-field="${escape(k)}" style="text-align:left;overflow:hidden"><span>${escape(k)}</span><b style="white-space:pre-wrap;overflow-wrap:anywhere">${escape(pretty(v).slice(0,160))}</b></button>`).join('')}</div>`;
      box.querySelectorAll('[data-field]').forEach(el=>el.onclick=()=>showInfo(el.dataset.field, pretty(data[el.dataset.field])));
      if(data.modules){const list=document.createElement('div');list.className='data-grid';list.innerHTML=Object.entries(data.modules).map(([name,row])=>`<button class="data-item" data-module="${escape(name)}" style="text-align:left"><span>${escape(name)}</span><b>${escape(row.status)}</b></button>`).join('');box.append(list);list.querySelectorAll('[data-module]').forEach(el=>el.onclick=()=>showInfo(el.dataset.module,pretty(data.modules[el.dataset.module])));}
    } else {
      visibleRows=detail==='activity'?state.activity||[]:detail==='incidents'?state.incidents||[]:state.tester.cases||[];
      box.innerHTML=(detail==='tests'?`<div class="test-summary"><p><strong>Verificações rápidas · diagnóstico operacional</strong><br>${escape(state.tester.summary)}<br>Não substitui a auditoria ampla de regressão.</p><button class="button subdued" id="test-again">Verificar</button><button class="button subdued" id="test-history">Histórico</button></div>`:'')+rows(visibleRows);
      box.querySelectorAll('[data-row]').forEach(el=>el.onclick=()=>showInfo('Evidência',pretty(visibleRows[Number(el.dataset.row)])));
      if($('test-again'))$('test-again').onclick=runTests;
      if($('test-history'))$('test-history').onclick=()=>showInfo('Histórico de diagnósticos', pretty(state.tester.history));
    }
  }
  let restartConfirmationPending = false;
  function openDialog(forRestart) {
    restartConfirmationPending = forRestart;
    // Native dialogs retain returnValue across openings, including Escape.
    $('action-dialog').returnValue = 'cancel';
    $('action-dialog').showModal();
  }
  function showInfo(title,description) {put('dialog-title',title);put('dialog-description',description);$('dialog-description').style.whiteSpace='pre-wrap';$('dialog-confirm').hidden=true;$('dialog-details').replaceChildren();openDialog(false);}
  async function runTests(){try{const r=await request('/api/tester/run',{});toast(r.accepted?'Verificações rápidas iniciadas · não é a regressão completa':r.reason);setDetail('tests');await poll();}catch(e){toast('Não foi possível iniciar: '+e.message);}}
  async function runRegression(){if(regressionBusy)return;regressionBusy=true;renderDetail();try{const r=await request('/api/regression/run',{});toast(r.accepted?'Regressão operacional solicitada · auditoria ampla permanece separada':'Regressão não iniciada: '+(r.reason||'motivo não informado'));setDetail('regression');await poll();}catch(e){toast('Não foi possível solicitar regressão: '+e.message);}finally{regressionBusy=false;renderDetail();}}
  function restart(){put('dialog-title','Religar o Trader?');put('dialog-description','O Brain continuará ativo. O servidor só será reiniciado se a verificação permitir: sem geração em andamento, identidade correta e orçamento disponível. Nenhuma ordem ou configuração de risco será alterada.');$('dialog-confirm').hidden=false;$('dialog-details').replaceChildren();openDialog(true);}
  $('action-dialog').addEventListener('cancel',()=>{restartConfirmationPending=false;$('action-dialog').returnValue='cancel';});
  $('action-dialog').addEventListener('close',async()=>{const confirmed=restartConfirmationPending&&$('action-dialog').returnValue==='confirm'&&!$('dialog-confirm').hidden;restartConfirmationPending=false;$('action-dialog').returnValue='cancel';if(!confirmed)return;toast('Verificando condições de reinício…');try{const r=await request('/api/brain/relight',{confirm:'RELIGHT_QUANT',reason:'explicit_user_panel'});toast(r.accepted?'Reinício acionado; verificando recuperação.':'Reinício não executado: '+r.reason);await poll();}catch(e){toast('Não foi possível confirmar o reinício: '+e.message);}});
  function appendReplyText(element,text){
    const content=String(text??'Resposta não disponível');
    const links=/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g;
    let cursor=0;
    for(const match of content.matchAll(links)){
      element.append(document.createTextNode(content.slice(cursor,match.index)));
      const link=document.createElement('a');link.textContent=match[1];link.href=match[2];link.target='_blank';link.rel='noopener noreferrer';link.style.color='#65ffab';element.append(link);
      cursor=match.index+match[0].length;
    }
    element.append(document.createTextNode(content.slice(cursor)));
  }
  function message(role,text,investigation,source){const div=document.createElement('div');div.className='chat-message '+role;div.style.cssText='padding:15px;margin:12px 0;border:1px solid #234732;border-radius:12px;white-space:pre-wrap;font-size:13px;line-height:1.65';const label=document.createElement('small');label.textContent=role==='user'?'VOCÊ':'JARVIS';label.style.cssText='display:block;color:#65ffab;letter-spacing:2px;margin-bottom:8px';div.append(label);appendReplyText(div,text);
    if(role==='assistant'&&source){const origin=document.createElement('small');origin.textContent='Fonte da resposta: '+String(source).slice(0,120);origin.style.cssText='display:block;color:#8baf99;margin-top:12px';div.append(origin);}
    if(role==='assistant'&&Array.isArray(investigation?.evidence)){for(const evidence of investigation.evidence.slice(0,10)){if(!evidence||typeof evidence!=='object')continue;const button=document.createElement('button');button.type='button';button.className='button subdued';button.style.cssText='display:block;white-space:normal;text-align:left;overflow-wrap:anywhere;margin-top:8px;width:100%';button.textContent=`[${String(evidence.id||'?').slice(0,40)}] ${String(evidence.line_ref||evidence.path||'Telemetria observada').slice(0,180)}`;button.onclick=()=>showInfo('Evidência consultada · não é uma instrução',pretty({id:evidence.id,path:evidence.path,line_ref:evidence.line_ref,hash:evidence.hash??evidence.sha256,snippet:evidence.snippet,observed_at:evidence.observed_at,fresh:evidence.fresh,facts:evidence.facts}));div.append(button);}}
    $('chat-messages').append(div);$('chat-messages').scrollTop=$('chat-messages').scrollHeight;}
  async function chat(text){if(busyChat||!text.trim())return null;busyChat=true;$('send-button').disabled=true;$('chat-working').hidden=false;$('chat-input').value='';message('user',text);try{const r=await request('/api/assistant/chat',{message:text});message('assistant',r.reply,r.investigation,r.source);if(speak)speakReply(r.reply);if(r.action==='confirm_restart')restart();if(r.action==='tester')setDetail('tests');if(r.action==='regression')setDetail('regression');return r;}catch(e){message('assistant','Não consegui consultar o núcleo: '+e.message);return null;}finally{busyChat=false;$('send-button').disabled=false;$('chat-working').hidden=true;poll();}}
  $('chat-form').onsubmit=e=>{e.preventDefault();chat($('chat-input').value);};
  $('chat-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();chat(e.target.value);}};
  document.querySelectorAll('[data-prompt]').forEach(el=>el.onclick=()=>chat(el.dataset.prompt));
  document.querySelectorAll('[data-pick]').forEach(el=>el.onclick=()=>pick(el.dataset.pick));
  document.querySelectorAll('[data-detail]').forEach(el=>el.onclick=()=>setDetail(el.dataset.detail));
  function showWorkspaceView(view){const linux=view==='linux';$('brain-view').hidden=linux;$('linux-view').hidden=!linux;if(linux){exitImmersive();loadLinuxActions();}}
  async function loadLinuxActions(){
    if(linuxBusy)return;linuxBusy=true;$('linux-refresh').disabled=true;
    try{
      const log=await request('/api/linux/actions'), runtime=log.runtime||{}, running=runtime.running||{};
      put('linux-service',log.service||'qhos2-brain.service');put('linux-pid',running.pid?`PID ${running.pid}`:'NÃO INFORMADO');put('linux-state',runtime.available===false?'INDISPONÍVEL':running.pid?'ATIVO':'NÃO CONFIRMADO');put('linux-code-state',runtime.loaded_code_state||'NÃO INFORMADO');
      const actions=Array.isArray(log.actions)?log.actions:[];put('linux-log-count',`${actions.length} EVENTO${actions.length===1?'':'S'}`);
      $('linux-log').innerHTML=actions.length?actions.slice().reverse().map(row=>`<article class="linux-log-row"><time>${escape(dateLabel(row.timestamp))}</time><span class="linux-log-prompt">›</span><div><strong>${escape(row.action||'evento')}</strong><p>${escape(row.detail||'Sem detalhe adicional.')}</p><small>${escape(row.outcome||'observed')}${row.pid?` · PID ${escape(row.pid)}`:''}</small></div></article>`).join(''):'<div class="linux-log-empty"><span>›</span> Nenhuma ação registrada nesta leitura.</div>';
    }catch(e){put('linux-state','SEM CONEXÃO');$('linux-log').innerHTML=`<div class="linux-log-empty error-text"><span>!</span> ${escape(e.message)}</div>`;}finally{linuxBusy=false;$('linux-refresh').disabled=false;}
  }
  document.querySelectorAll('[data-view]').forEach(el=>el.onclick=()=>{document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b===el);b.setAttribute('aria-pressed',String(b===el));});const v=el.dataset.view;showWorkspaceView(v);if(v==='linux')return;if(v==='server')pick('trader');else if(v==='tester')setDetail('tests');else if(v==='cognition')setDetail('cognition');else if(v==='incidents')setDetail('incidents');else setDetail('activity');});
  $('linux-refresh').onclick=loadLinuxActions;
  $('run-tests').onclick=runTests;$('restart-button').onclick=restart;$('refresh-button').onclick=()=>{poll();toast('Atualizando telemetria');};$('target-button').onclick=()=>pick('target');
  $('audit-button').onclick=()=>{if(state){setDetail('audit');$('detail-panel').scrollIntoView?.({behavior:'smooth',block:'nearest'});}else toast('Aguardando conexão para consultar o relatório amplo.');};
  $('regression-button').onclick=()=>{if(state){setDetail('regression');$('detail-panel').scrollIntoView?.({behavior:'smooth',block:'nearest'});}else toast('Aguardando conexão para consultar a regressão operacional.');};
  $('help-button').onclick=()=>showInfo('Seu Brain, por dentro','O cérebro permanece animado e pode ser arrastado para mudar o ângulo. Expanda a visualização e clique em Cognição, Trader, Diagnóstico ou Proteção: a câmera entra no sistema e distribui todas as ações na cena. Use o microfone para navegar, solicitar diagnósticos ou conversar com o Jarvis. A aba Linux mostra apenas o registro estruturado do serviço; não é um terminal. O tester não opera no mercado, e qualquer pedido de reinício continua sujeito a confirmação e guardas.');
  $('sound-button').onclick=()=>{speak=!speak;$('sound-button').setAttribute('aria-pressed',String(speak));if(speak){const status='Voz neural local ativada';voiceFeedback(status);toast(status);speakReply('Voz ativada. Estou à sua disposição.');}else{window.speechSynthesis?.cancel();stopNeuralVoice();voiceFeedback('Leitura em voz alta desativada.');toast('Leitura em voz alta desativada');}};
  function voiceFeedback(text){put('voice-command-status',text);put('voice-note',text);}
  function navigateByVoice(key,detailName='inspector'){if(coreExpanded())enterCapability(key);else{selectSystem(key);setDetail(detailName);}}
  async function handleVoiceCommand(transcript){
    const command=normalizeVoice(transcript);$('chat-input').value=transcript;voiceFeedback(`Ouvido: “${transcript}”`);
    const navigation=command.match(/^(?:abrir|abra|mostrar|mostre|entrar|entre)\s+(?:(?:no|na|o|a)\s+)?(diagnostico|testes|regressao|cognicao|protecao|trader|servidor)[.!]?$/);
    if(navigation){
      const destinations={diagnostico:['tester','tests'],testes:['tester','tests'],regressao:['tester','regression'],cognicao:['brain','cognition'],protecao:['watchdog','incidents'],trader:['trader'],servidor:['trader']};
      navigateByVoice(...destinations[navigation[1]]);return;
    }
    const response=await chat(transcript);if(response&&coreExpanded())showImmersiveResult('Resposta do Brain',response.reply||response,'Comando de voz encaminhado ao Jarvis.');
  }
  function startVoiceCommand(){
    const API=window.SpeechRecognition||window.webkitSpeechRecognition;if(!API){voiceFeedback('Comando de voz não disponível neste navegador.');toast('Comando de voz não disponível neste navegador.');return;}
    if(voiceRecognition){voiceRecognition.stop();return;}
    const rec=new API();voiceRecognition=rec;rec.lang='pt-BR';rec.continuous=false;rec.interimResults=false;
    rec.onstart=()=>{[$('mic-button'),$('scene-voice-button'),$('immersive-voice-button')].forEach(button=>button?.classList.toggle('mic-active',true));voiceFeedback('Ouvindo… diga um comando ou faça uma pergunta.');};
    rec.onresult=e=>{const transcript=e.results?.[0]?.[0]?.transcript?.trim();if(transcript)handleVoiceCommand(transcript);};
    rec.onerror=e=>{const reason=e.error==='not-allowed'?'permissão do microfone recusada':e.error||'erro não informado';voiceFeedback('Voz indisponível: '+reason);toast('Voz indisponível: '+reason);};
    rec.onend=()=>{voiceRecognition=null;[$('mic-button'),$('scene-voice-button'),$('immersive-voice-button')].forEach(button=>button?.classList.toggle('mic-active',false));};
    try{rec.start();}catch(e){voiceRecognition=null;voiceFeedback('Não foi possível iniciar o microfone.');}
  }
  $('mic-button').onclick=startVoiceCommand;$('scene-voice-button').onclick=startVoiceCommand;$('immersive-voice-button').onclick=startVoiceCommand;
  $('immersive-back').onclick=()=>immersiveResult?renderImmersiveMenu():exitImmersive();
  document.querySelectorAll('[data-mode]').forEach(el=>el.onclick=()=>{mode=el.dataset.mode;document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('selected',b===el);b.setAttribute('aria-pressed',String(b===el));});put('core-heading',mode==='neural'?'Centro cognitivo':'Núcleo orbital');});
  $('reset-view').onclick=()=>{rotation=.3;tilt=-.12;if(!$('immersive-layer').hidden)exitImmersive();toast('Perspectiva centralizada');};
  function updateExpandedState(){const expanded=coreExpanded();$('fullscreen-button').setAttribute('aria-label',expanded?'Sair da visualização expandida':'Expandir visualização');$('fullscreen-button').setAttribute('title',expanded?'Sair da visualização expandida':'Expandir visualização');if(!expanded)exitImmersive();}
  $('fullscreen-button').onclick=async()=>{const panel=$('core-panel');try{if(document.fullscreenElement===panel)await document.exitFullscreen();else if(panel.classList.contains('expanded'))panel.classList.remove('expanded');else await panel.requestFullscreen();}catch(e){panel.classList.toggle('expanded');updateExpandedState();}};
  document.addEventListener?.('fullscreenchange',updateExpandedState);
  // Ambient rotation is identity, not evidence of reasoning or market activity.
  const canvas=$('brain-canvas'),ctx=canvas.getContext('2d');
  let width=1,height=1,dpr=1,animTime=0,previous=performance.now(),raf=0,intersecting=true,disposed=false,frames=0,lastFrame=0;
  const paused=()=>window.QHOSRithmicObservation?.motionPaused()||false;
  function visible(){return !document.hidden&&intersecting&&!$('brain-view').hidden&&canvas.getClientRects().length>0;}
  function wake(){if(!raf&&!disposed&&visible())raf=requestAnimationFrame(draw);}
  function resize(){const r=canvas.getBoundingClientRect();width=r.width;height=r.height;dpr=Math.min(devicePixelRatio||1,2);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);wake();}
  const sizeObserver=new ResizeObserver(resize);sizeObserver.observe(canvas);
  const sceneObserver=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{intersecting=entries[0].isIntersecting;if(!intersecting){cancelAnimationFrame(raf);raf=0;}else wake();}):{observe(){},disconnect(){}};sceneObserver.observe(canvas);
  canvas.addEventListener('pointerdown',e=>{dragging=true;lastPointer=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);wake();});
  canvas.addEventListener('pointermove',e=>{if(!dragging)return;rotation+=(e.clientX-lastPointer[0])*.009;tilt=Math.max(-.9,Math.min(.9,tilt+(e.clientY-lastPointer[1])*.006));lastPointer=[e.clientX,e.clientY];wake();});
  canvas.addEventListener('pointerup',()=>{dragging=false;wake();});canvas.addEventListener('pointercancel',()=>dragging=false);
  const nodes=[];for(const hemi of [-1,1])for(let i=0;i<420;i++){const u=i/419,z=1-2*u,phi=i*2.399963,ring=Math.sqrt(1-z*z),x=Math.cos(phi)*ring,y=Math.sin(phi)*ring,wrinkle=1+.075*Math.sin(phi*4+z*14);nodes.push({x:hemi*(.022+.91*(x+1)/2)*wrinkle,y:z*.69*wrinkle,z:y*.6,seed:i*.83+hemi});}
  const edges=[];for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){const a=nodes[i],b=nodes[j];if(Math.hypot(a.x-b.x,a.y-b.y,a.z-b.z)<.18)edges.push([i,j]);}
  function project(n,scale){const x=n.x*Math.cos(rotation)+n.z*Math.sin(rotation),z=-n.x*Math.sin(rotation)+n.z*Math.cos(rotation),y=n.y*Math.cos(tilt)-z*Math.sin(tilt),depth=n.y*Math.sin(tilt)+z*Math.cos(tilt),p=1/(1+depth*.16);return{x:width/2+x*scale*p,y:height*.49+y*scale*p,z:depth};}
  function arc(r,start,end,alpha,line=1){ctx.beginPath();ctx.arc(width/2,height*.49,r,start,end);ctx.strokeStyle=`rgba(101,255,171,${alpha})`;ctx.lineWidth=line;ctx.stroke();}
  function draw(now){
    raf=0;if(!visible()||disposed)return;
    if(!paused()&&now-lastFrame<32){wake();return;}
    const dt=Math.min((now-previous)/1000,.06);previous=now;lastFrame=now;frames++;
    const moving=!paused(),busy=telemetryFresh&&now<activityUntil;
    if(moving){animTime+=dt;if(!dragging)rotation+=dt*.07;sceneZoom+=(targetSceneZoom-sceneZoom)*Math.min(1,dt*3.2);}else sceneZoom=targetSceneZoom;
    ctx.clearRect(0,0,width,height);const scale=Math.min(width*.25,height*.32)*sceneZoom,t=animTime;
    const glow=ctx.createRadialGradient(width/2,height*.49,0,width/2,height*.49,scale*1.45);glow.addColorStop(0,'rgba(45,215,122,.10)');glow.addColorStop(1,'rgba(0,0,0,0)');ctx.fillStyle=glow;ctx.fillRect(0,0,width,height);
    for(let ring=0;ring<3;ring++){const radius=scale*(1.22+ring*.15);arc(radius,0,Math.PI*2,.1);for(let seg=0;seg<12;seg++){const start=seg*Math.PI/6+(ring%2?-1:1)*t*(.04+ring*.01);arc(radius,start,start+.24,.24,ring===1?2:1);}}
    if(mode==='orbital'){for(let k=0;k<6;k++)arc(scale*(.19+k*.077),t*(k%2?-.13:.17),t*(k%2?-.13:.17)+Math.PI*1.6,.3,2);arc(scale*.12,0,Math.PI*2,.65,3);}
    else{
      const points=nodes.map(n=>project(n,scale));
      // Batch the existing neural mesh into eight depth layers.
      for(let bucket=0;bucket<8;bucket++){ctx.beginPath();for(const[a,b]of edges){const p=points[a],q=points[b];if(Math.max(0,Math.min(7,Math.floor((p.z+1)*4)))!==bucket)continue;ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);}ctx.strokeStyle=`rgba(83,238,153,${.18-bucket*.017})`;ctx.lineWidth=.65;ctx.stroke();}
      points.forEach((p,i)=>{const pulse=busy&&moving?(Math.sin(t*2+nodes[i].seed)+1)/2:.25;ctx.fillStyle=`rgba(123,255,179,${.35+pulse*.4})`;ctx.beginPath();ctx.arc(p.x,p.y,.8+pulse*.55+(1-p.z)*.25,0,Math.PI*2);ctx.fill();});
      if(busy&&moving){let count=0;for(const[a,b]of edges){if((a*7+b)%137!==0||count++>=20)continue;const p=points[a],q=points[b],v=(t*.4+a*.071)%1;ctx.fillStyle='rgba(184,255,207,.8)';ctx.beginPath();ctx.arc(p.x+(q.x-p.x)*v,p.y+(q.y-p.y)*v,1.3,0,Math.PI*2);ctx.fill();}}
    }
    const entry=rithmicVisual?.nodes.find(n=>n.id==='reader'),delivered=rithmicVisual?.edges.some(e=>e.from==='reader'&&e.to==='consumers'&&e.active);
    if(rithmicVisual?.valid&&!$('brain-stage').classList.contains('immersive-active')){
      const x0=Math.min(180,width*.26),y0=height*.49,x1=width/2-scale*.75;
      ctx.beginPath();ctx.moveTo(x0,y0);ctx.bezierCurveTo(x0+30,y0-32,x1-25,y0-32,x1,y0);ctx.strokeStyle=entry?.active?'#65ffab80':'#65ffab25';ctx.lineWidth=1;ctx.stroke();
      if(entry?.active&&delivered&&moving){for(let i=0;i<3;i++){const u=(t*.3+i/3)%1;ctx.fillStyle='#a0ffbd';ctx.beginPath();ctx.arc(x0+(x1-x0)*u,y0-96*u*(1-u),1.8,0,Math.PI*2);ctx.fill();}}
    }
    document.querySelector('[data-pick=brain]')?.setAttribute('data-work',busy?'processing':'idle');
    if(moving||dragging||Math.abs(targetSceneZoom-sceneZoom)>.01)wake();
  }
  function receiveVisual(e){rithmicVisual=e.detail;const node=rithmicVisual.nodes.find(n=>n.id==='reader');put('rithmic-entry-state',rithmicVisual.environment+' · '+window.QHOSRithmicObservation.labels[node.state]);$('rithmic-entry').dataset.active=String(node.active);wake();}
  window.addEventListener('qhos-rithmic-visual',receiveVisual);
  $('rithmic-entry').onclick=()=>window.QHOSRithmicObservation?.select('reader');
  function motionUpdate(){const p=paused();$('motion-toggle').setAttribute('aria-pressed',String(p));$('motion-toggle').setAttribute('aria-label',p?'Retomar movimento ambiente':'Pausar movimento ambiente');cancelAnimationFrame(raf);raf=0;wake();}
  $('motion-toggle').onclick=()=>window.QHOSRithmicObservation?.setMotion(!paused());window.addEventListener('qhos-motion',motionUpdate);
  document.querySelectorAll('[data-mode], [data-view], #reset-view, [data-pick], #immersive-back').forEach(el=>el.addEventListener('click',wake));
  const pollTimer=setInterval(()=>{if(!document.hidden)poll();},4000),clockTimer=setInterval(()=>{if(!document.hidden)put('clock',new Date().toLocaleTimeString('pt-BR'));},1000);
  function visibility(){if(document.hidden){cancelAnimationFrame(raf);raf=0;}else{previous=performance.now();poll();wake();}}
  document.addEventListener('visibilitychange',visibility);
  function disposeScene(){disposed=true;cancelAnimationFrame(raf);clearInterval(pollTimer);clearInterval(clockTimer);activeRequests.forEach(c=>c.abort());sizeObserver.disconnect();sceneObserver.disconnect();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('qhos-rithmic-visual',receiveVisual);window.removeEventListener('qhos-motion',motionUpdate);stopNeuralVoice();voiceRecognition?.stop();}
  window.addEventListener('pagehide',disposeScene,{once:true});
  window.QHOSBrainVisual={stats:()=>({frames,rotation,raf,visible:visible(),paused:paused(),nodes:nodes.length,edges:edges.length}),dispose:disposeScene};
  wake();poll();

})();
/* ===== QHOS_FRONTEND_DS_BRAIN ========================================
   1. Alternancia Comando / Assistente em telas estreitas.
   2. Faixa de governanca: revisao do Brain, MATCH/STALE/drift, alcance do
      Trader e as travas de execucao — separada do fio da conversa.
   Nenhuma requisicao nova: o estado e lido por clone da resposta que o
   proprio app.js ja busca em /api/assistant/state.
   ==================================================================== */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const U = () => window.QHOSUI;

  /* --- 1. alternancia de visao em telas estreitas ----------------------- */
  const troca = document.createElement('div');
  troca.id = 'qhos-brain-switch';
  troca.setAttribute('role', 'group');
  troca.setAttribute('aria-label', 'Alternar entre comando e assistente');
  const botoes = [['command', 'Comando'], ['chat', 'Assistente']].map(([v, rotulo]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = rotulo;
    b.dataset.qhosView = v;
    b.setAttribute('aria-pressed', 'false');
    b.onclick = () => aplicaVisao(v, true);
    troca.appendChild(b);
    return b;
  });
  function aplicaVisao(v, foco) {
    document.body.dataset.qhosView = v;
    botoes.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.qhosView === v)));
    try { localStorage.setItem('qhos-brain-view', v); } catch {}
    if (foco && v === 'chat') {
      const lista = $('chat-messages');
      if (lista) lista.scrollTop = lista.scrollHeight;
      $('chat-form')?.querySelector('textarea')?.focus({preventScroll: true});
    }
  }
  document.querySelector('.topbar-end')?.insertAdjacentElement('beforebegin', troca);
  let visaoInicial = 'command';
  try { visaoInicial = localStorage.getItem('qhos-brain-view') || 'command'; } catch {}
  aplicaVisao(visaoInicial === 'chat' ? 'chat' : 'command', false);

  // a altura real da topbar alimenta o calculo do painel em modo assistente
  function mediaTopbar() {
    const t = document.querySelector('.topbar');
    if (t) document.documentElement.style.setProperty(
      '--qhos-topbar', Math.round(t.getBoundingClientRect().height) + 'px');
  }
  mediaTopbar();
  addEventListener('resize', mediaTopbar);

  /* --- 2. faixa de governanca ------------------------------------------ */
  const gov = document.createElement('section');
  gov.id = 'qhos-gov';
  gov.setAttribute('aria-label', 'Governança operacional');
  gov.hidden = true;
  const alertas = document.createElement('div');
  alertas.id = 'qhos-alertas';
  alertas.setAttribute('role', 'status');
  const alvo = document.querySelector('.command-column .metrics');
  alvo?.insertAdjacentElement('beforebegin', gov);
  alvo?.insertAdjacentElement('beforebegin', alertas);

  function pares(itens) {
    const sig = JSON.stringify(itens);
    if (gov.dataset.sig === sig) return;
    gov.dataset.sig = sig;
    gov.replaceChildren(...itens.map(([rotulo, valor, estado, motivo]) => {
      const par = document.createElement('div');
      par.className = 'qhos-par';
      const b = document.createElement('b');
      b.textContent = rotulo;
      par.appendChild(b);
      if (estado && U()) {
        par.appendChild(U().badge(document.createElement('span'), estado,
                                  {label: valor, reason: motivo, live: false}));
      } else {
        const s = document.createElement('span');
        s.textContent = valor;
        par.appendChild(s);
      }
      return par;
    }));
    gov.hidden = itens.length === 0;
  }

  const TOM = {stale: '#edc67b', error: '#ff8790', blocked: '#c4b5fd',
               unavailable: '#8fa79b', unknown: '#8fa79b'};
  function pintaAlertas(itens) {
    const sig = JSON.stringify(itens);
    if (alertas.dataset.sig === sig) return;
    alertas.dataset.sig = sig;
    alertas.replaceChildren(...itens.map(({estado, texto}) => {
      const d = document.createElement('div');
      d.style.setProperty('--qhos-tone', TOM[estado] || '#edc67b');
      if (U()) d.appendChild(U().badge(document.createElement('span'), estado, {live: false}));
      const p = document.createElement('p');
      p.textContent = texto;
      d.appendChild(p);
      return d;
    }));
  }

  function governanca(estado, autoridade) {
    if (!U() || !estado) return;
    const t = estado.trader || {};
    const at = autoridade?.runtime_attestation || {};
    const ev = autoridade?.trader_event_stream || {};
    const obs = t.rithmic_protocol_observation || {};
    const itens = [], avisos = [];

    // 2a. revisao carregada pelo Brain e desvio
    if (at.loaded_code_state) {
      const bom = at.loaded_code_state === 'MATCH' && at.stale_runtime !== true
                  && (at.drift || []).length === 0;
      itens.push(['Brain', at.loaded_code_state + (at.running?.source_revision
                  ? ' · ' + String(at.running.source_revision).slice(0, 12) : ''),
                  bom ? 'ready' : 'stale',
                  bom ? 'Revisão carregada confere com a origem declarada.'
                      : 'Divergência: ' + ((at.drift || []).join(' · ') || 'runtime marcado como antigo')]);
      if (!bom) {
        avisos.push({estado: 'stale', texto:
          'A revisão carregada pelo Brain não confere com a origem declarada'
          + ((at.drift || []).length ? ': ' + at.drift.join(' · ') : '.')
          + ' Leituras deste painel podem não corresponder ao código em execução.'});
      }
    } else {
      itens.push(['Brain', 'atestação ausente', 'unknown',
                  'O serviço não expôs runtime_attestation nesta leitura.']);
    }

    // 2b. alcance do Trader
    const indeterminado = t.connection_state === 'INDETERMINATE' || t.telemetry_partial || t.stale;
    itens.push(['Trader', t.reachable ? (indeterminado ? 'alcançável · telemetria parcial' : 'alcançável')
                                      : 'fora de alcance',
                t.reachable ? (indeterminado ? 'stale' : 'ready') : 'disconnected',
                t.reachable
                  ? (indeterminado ? 'A telemetria disponível não confirma prontidão.'
                                   : 'Health confirmou o processo.')
                  : 'Nenhum sinal operacional confirmado.']);
    if (Number.isFinite(t.heartbeat_age_seconds)) {
      itens.push(['Heartbeat', Math.round(t.heartbeat_age_seconds) + ' s', null, null]);
    }
    if (t.loaded_code_state) {
      itens.push(['Código do Trader', String(t.loaded_code_state),
                  t.loaded_code_state === 'MATCH' ? 'ready' : 'stale', null]);
    }

    // 2c. fluxo de eventos do Trader
    if (ev.enabled !== undefined) {
      const idade = Number.isFinite(ev.last_message_age_s) ? ev.last_message_age_s : null;
      itens.push(['Fluxo de eventos',
                  ev.connected ? (idade === null ? 'conectado' : 'conectado · ' + idade.toFixed(1) + ' s')
                               : 'desconectado',
                  ev.connected ? (idade !== null && idade <= 15 ? 'receiving' : 'stale') : 'disconnected',
                  ev.last_error || null]);
    }

    // 2d. travas de execução — permanecem visíveis porque são o estado real
    const trava = obs.can_execute === false && obs.book_valid === false;
    itens.push(['Execução',
                'can_execute=' + String(obs.can_execute) + ' · book_valid=' + String(obs.book_valid),
                trava ? 'blocked' : 'unknown',
                trava ? 'Travas ativas. Esta interface observa; não autoriza operação.'
                      : 'O contrato não afirmou as travas nesta leitura.']);

    // 2e. cotações amostradas do Trader
    const sq = t.sampled_market_quotes || {};
    if (sq.state) {
      itens.push(['Cotações amostradas', String(sq.state).toLowerCase(),
                  sq.state === 'FRESH' ? 'ready' : sq.state === 'STALE' ? 'stale' : 'unknown',
                  sq.reason || null]);
      if (sq.state !== 'FRESH') {
        avisos.push({estado: 'stale', texto:
          'As cotações amostradas do Trader estão em ' + String(sq.state).toLowerCase()
          + (sq.reason ? ' (' + sq.reason + ')' : '')
          + '. Nenhum número de mercado neste painel representa o instante atual.'});
      }
    }

    pares(itens);
    pintaAlertas(avisos);
    U().melhoraTooltips(document);
  }

  /* --- 3. leitura sem requisicao nova -----------------------------------
     O app.js ja busca /api/assistant/state a cada 4 s; aqui so observamos a
     resposta por clone. /api/brain/authority nao e buscado pelo app no ciclo
     normal, entao a atestacao e lida uma unica vez na carga — sem timer. */
  let autoridade = null;
  const fetchOriginal = window.fetch;
  window.fetch = function (entrada, opcoes) {
    const p = fetchOriginal.apply(this, arguments);
    try {
      const url = typeof entrada === 'string' ? entrada : entrada?.url || '';
      if (url.includes('/api/assistant/state')) {
        p.then(r => { if (r && r.ok) r.clone().json()
          .then(d => { try { governanca(d, autoridade); } catch (e) { console.warn('[qhos-ds]', e); } })
          .catch(() => {}); }).catch(() => {});
      }
    } catch {}
    return p;
  };
  fetchOriginal('/api/brain/authority', {cache: 'no-store'})
    .then(r => r.ok ? r.json() : null)
    .then(a => { autoridade = a; })
    .catch(() => {});

  if (window.QHOSUI) window.QHOSUI.melhoraTooltips(document);
})();
