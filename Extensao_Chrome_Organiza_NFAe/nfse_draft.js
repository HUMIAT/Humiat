(function(){
  'use strict';

  const BOX='organiza-nfse-draft-box';
  const DEBUG_LOG_KEY='organiza_nfse_debug_steps_v1';
  const SESSION_PAYLOAD='organiza_nfse_payload';
  const SESSION_FP='organiza_nfse_payload_fp';
  const SESSION_ACTIVE='organiza_nfse_active';
  const SESSION_PAUSED='organiza_nfse_paused';
  const SESSION_CHECKPOINT='organiza_nfse_checkpoint';
  const SESSION_READY_SENT='organiza_nfse_ready_notified';

  let payload=null;
  let payloadFp='';
  let running=false;
  let runTimer=null;
  let renderTimer=null;

  const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
  const norm=(v)=>String(v||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/\s+/g,' ').trim();
  const visible=(el)=>{try{const s=getComputedStyle(el);return s.display!=='none'&&s.visibility!=='hidden'&&!!(el.offsetWidth||el.offsetHeight||el.getClientRects().length)}catch(_){return true}};
  const fire=(el)=>{['input','change','blur'].forEach(t=>{try{el.dispatchEvent(new Event(t,{bubbles:true}))}catch(_){}})};

  function fingerprint(p){
    try{return String((p&&p.rascunho_id)||'')+'|'+String((p&&p.tomador&&p.tomador.documento)||'')+'|'+String((p&&p.competencia)||'')+'|'+String((p&&p.servico&&p.servico.valor)||'')}
    catch(_){return String(Date.now())}
  }
  function getActive(){try{return sessionStorage.getItem(SESSION_ACTIVE)==='1'}catch(_){return false}}
  function setActive(v){try{sessionStorage.setItem(SESSION_ACTIVE,v?'1':'0')}catch(_){}}
  function getPaused(){try{return sessionStorage.getItem(SESSION_PAUSED)==='1'}catch(_){return false}}
  function setPaused(v){try{sessionStorage.setItem(SESSION_PAUSED,v?'1':'0')}catch(_){}}
  function getCheckpoint(){try{return sessionStorage.getItem(SESSION_CHECKPOINT)||''}catch(_){return ''}}
  function setCheckpoint(v){try{if(v)sessionStorage.setItem(SESSION_CHECKPOINT,v);else sessionStorage.removeItem(SESSION_CHECKPOINT)}catch(_){}}

  function readDebugSteps(){try{const a=JSON.parse(localStorage.getItem(DEBUG_LOG_KEY)||'[]');return Array.isArray(a)?a:[]}catch(_){return []}}
  function clearDebugSteps(){try{localStorage.removeItem(DEBUG_LOG_KEY)}catch(_){}}
  function addDebugStep(msg,detail=''){
    const m=String(msg||'').trim(), d=String(detail||'').trim(); if(!m&&!d)return;
    try{
      const arr=readDebugSteps(), now=Date.now(), last=arr[arr.length-1];
      if(last&&last.msg===m&&last.detail===d){last.t=now;last.count=Number(last.count||1)+1}
      else arr.push({t:now,msg:m,detail:d,count:1});
      while(arr.length>140)arr.shift();
      localStorage.setItem(DEBUG_LOG_KEY,JSON.stringify(arr));
    }catch(_){}
  }
  function detectStage(){
    const path=norm(location.pathname), body=norm(document.body&&document.body.innerText);

    // IMPORTANTE: primeiro use a URL oficial do wizard. A página /DPS/Tributacao
    // contém textos como "serviço prestado" e, se o corpo for analisado antes da URL,
    // ela pode ser confundida com /DPS/Servico. Isso fazia a automação acreditar que o
    // Avançar do Serviço não tinha funcionado mesmo já estando na etapa Valores.
    if(path.includes('/dps/pessoas')) return 'pessoas';
    if(path.includes('/dps/servico')) return 'servico';
    if(path.includes('/dps/tributacao')||path.includes('/dps/valores')) return 'valores';
    if(path.includes('/dps/substituicao')||path.includes('/dps/emitir')||path.includes('/dps/resumo')||path.includes('/dps/revis')) return 'revisao';

    // Fallback apenas para páginas sem uma rota reconhecida.
    if(
      (body.includes('emitir nfs-e')&&(body.includes('revise sua')||body.includes('revisao da declaracao'))) ||
      body.includes('previa dos valores da nfs-e') || body.includes('prévia dos valores da nfs-e')
    ) return 'revisao';
    if(body.includes('informacoes gerais')&&body.includes('tomador')) return 'pessoas';
    if(body.includes('local do fornecimento/prestacao do servico')&&body.includes('codigo de tributacao nacional')) return 'servico';
    if(body.includes('valores do fornecimento/servico prestado')&&body.includes('tributacao municipal')) return 'valores';
    return 'desconhecida';
  }
  function formatDebugLog(){
    const arr=readDebugSteps(), lines=[];
    lines.push('Organiza → NFS-e | Log de diagnóstico');
    lines.push(`Versão da extensão: ${chrome?.runtime?.getManifest?.().version||'desconhecida'}`);
    lines.push(`Página: ${location.href}`);
    lines.push(`Etapa detectada: ${detectStage()}`);
    lines.push(`Checkpoint: ${getCheckpoint()||'(vazio)'}`);
    lines.push(`Automação ativa: ${getActive()?'sim':'não'}`);
    lines.push(`Automação pausada: ${getPaused()?'sim':'não'}`);
    lines.push(`Rascunho ID: ${(payload&&payload.rascunho_id)||'(não informado)'}`);
    lines.push('');lines.push('Etapas:');
    if(!arr.length)lines.push('(sem etapas registradas)');
    for(const item of arr){const dt=new Date(Number(item.t||Date.now()));const hh=String(dt.getHours()).padStart(2,'0'),mm=String(dt.getMinutes()).padStart(2,'0'),ss=String(dt.getSeconds()).padStart(2,'0');lines.push(`${hh}:${mm}:${ss} ${item.msg}${Number(item.count||1)>1?' ×'+item.count:''}`);if(item.detail)lines.push('  '+item.detail)}
    return lines.join('\n');
  }
  async function copyDebugLog(){
    const text=formatDebugLog();
    try{if(navigator.clipboard&&navigator.clipboard.writeText){await navigator.clipboard.writeText(text);return true}}catch(_){}
    try{const ta=document.createElement('textarea');ta.value=text;ta.setAttribute('readonly','');ta.style.cssText='position:fixed;left:-9999px;top:-9999px;opacity:0';document.documentElement.appendChild(ta);ta.select();ta.setSelectionRange(0,ta.value.length);const ok=document.execCommand('copy');ta.remove();return !!ok}catch(_){return false}
  }
  function renderLog(box){
    const host=box&&box.querySelector('#organiza-nfse-log'); if(!host)return;
    const arr=readDebugSteps().slice(-14); host.innerHTML='';
    if(!arr.length){const e=document.createElement('div');e.textContent='Aguardando etapas...';e.style.color='#777';host.appendChild(e);return}
    for(const item of arr){const row=document.createElement('div');row.style.cssText='padding:3px 0;border-bottom:1px solid #eee;line-height:1.25';const dt=new Date(Number(item.t||Date.now()));const hh=String(dt.getHours()).padStart(2,'0'),mm=String(dt.getMinutes()).padStart(2,'0'),ss=String(dt.getSeconds()).padStart(2,'0');const h=document.createElement('div');h.style.cssText='font-weight:700;color:#1f2937';h.textContent=`${hh}:${mm}:${ss}  ${item.msg}${Number(item.count||1)>1?'  ×'+item.count:''}`;row.appendChild(h);if(item.detail){const d=document.createElement('div');d.style.cssText='color:#555;margin-top:1px';d.textContent=item.detail;row.appendChild(d)}host.appendChild(row)}
    host.scrollTop=host.scrollHeight;
  }
  function ensureBox(msg='',detail=''){
    let el=document.getElementById(BOX);
    if(!el){
      el=document.createElement('div');el.id=BOX;el.style.cssText='position:fixed;right:14px;bottom:14px;z-index:2147483647;width:440px;max-width:calc(100vw - 28px);background:#fff;border:2px solid #15803d;border-radius:10px;padding:12px;font:13px Arial;color:#172033;box-shadow:0 12px 32px #0003';
      el.innerHTML='<strong>Organiza → NFS-e</strong><div id="organiza-nfse-msg" style="margin-top:6px"></div><small id="organiza-nfse-detail" style="display:block;margin-top:4px;color:#64748b"></small><div style="font-size:11px;font-weight:700;margin:8px 0 3px">Etapas da automação</div><div id="organiza-nfse-log" style="font-size:10px;max-height:160px;overflow:auto;background:#f8fafc;border:1px solid #dbe3ef;border-radius:6px;padding:6px;margin-bottom:8px;min-width:390px"></div><button id="organiza-nfse-start" style="margin-right:6px;padding:6px 9px;cursor:pointer">Iniciar automático</button><button id="organiza-nfse-pause" style="margin-right:6px;padding:6px 9px;cursor:pointer">Pausar</button><button id="organiza-nfse-copylog" style="margin-right:6px;padding:6px 9px;cursor:pointer">Copiar log</button><button id="organiza-nfse-clearlog" style="margin-right:6px;padding:6px 9px;cursor:pointer">Limpar log</button><button id="organiza-nfse-clear" style="padding:6px 9px;cursor:pointer">Limpar dados</button><div style="margin-top:8px;font-weight:bold;color:#b45309">A extensão nunca clica em “Emitir NFS-e”.</div>';
      document.documentElement.appendChild(el);
      el.querySelector('#organiza-nfse-start').addEventListener('click',()=>{setActive(true);setPaused(false);setCheckpoint('inicio');try{sessionStorage.removeItem(SESSION_READY_SENT)}catch(_){}clearDebugSteps();addDebugStep('Automação iniciada.','Etapas reiniciadas. Acompanhe o log para identificar qualquer campo que não seja localizado.');renderLog(el);scheduleRun(100,true)});
      el.querySelector('#organiza-nfse-pause').addEventListener('click',()=>{const p=!getPaused();setPaused(p);if(!p)setActive(true);addDebugStep(p?'Automação pausada.':'Automação retomada.',p?'Você pode alterar qualquer campo manualmente.':'Continuando a partir do checkpoint atual.');updateBox();if(!p)scheduleRun(100,true)});
      el.querySelector('#organiza-nfse-copylog').addEventListener('click',async()=>{const ok=await copyDebugLog();addDebugStep(ok?'Log copiado.':'Não consegui copiar o log.',ok?'Cole aqui no ChatGPT.':'Tente novamente ou copie manualmente.');updateBox()});
      el.querySelector('#organiza-nfse-clearlog').addEventListener('click',()=>{clearDebugSteps();addDebugStep('Log limpo.','Pronto para um novo teste.');updateBox()});
      el.querySelector('#organiza-nfse-clear').addEventListener('click',async()=>{try{await chrome.runtime.sendMessage({type:'NFSE_CLEAR_PAYLOAD'})}catch(_){}payload=null;payloadFp='';setActive(false);setPaused(false);setCheckpoint('');try{sessionStorage.removeItem(SESSION_PAYLOAD);sessionStorage.removeItem(SESSION_FP);sessionStorage.removeItem(SESSION_READY_SENT)}catch(_){}addDebugStep('Dados da NFS-e limpos.','A automação foi encerrada.');updateBox()});
      if(!renderTimer)renderTimer=setInterval(()=>{const b=document.getElementById(BOX);if(b){renderLog(b);const p=b.querySelector('#organiza-nfse-pause');if(p)p.textContent=getPaused()?'Continuar automação':'Pausar'}},400);
    }
    const m=el.querySelector('#organiza-nfse-msg');if(m)m.textContent=msg||statusMessage();
    const d=el.querySelector('#organiza-nfse-detail');if(d)d.textContent=detail||statusDetail();
    const p=el.querySelector('#organiza-nfse-pause');if(p)p.textContent=getPaused()?'Continuar automação':'Pausar';
    renderLog(el); return el;
  }
  function statusMessage(){if(getPaused())return 'Automação pausada.';if(!getActive())return getCheckpoint()==='rascunho_pronto'?'Rascunho pronto para conferência.':'Automação parada.';return 'Automação em andamento.'}
  function statusDetail(){const cp=getCheckpoint();return cp?`Checkpoint: ${cp}`:'Aguardando início.'}
  function updateBox(msg='',detail=''){ensureBox(msg,detail)}

  function findField(words,selector='input,textarea,select'){
    const wants=words.map(norm), els=[...document.querySelectorAll(selector)].filter(visible);
    for(const el of els){const own=textOf(el);if(wants.every(w=>own.includes(w)))return el;let label='';try{if(el.id){const l=document.querySelector('label[for="'+CSS.escape(el.id)+'"]');if(l)label=textOf(l)}}catch(_){}if(!label){try{const l=el.closest('label');if(l)label=textOf(l)}catch(_){}}if(wants.every(w=>(own+' '+label).includes(w)))return el}
    for(const lab of [...document.querySelectorAll('label,span,div,p')].filter(visible)){const lt=norm(lab.textContent);if(!wants.every(w=>lt.includes(w)))continue;let f=null;try{if(lab.htmlFor)f=document.getElementById(lab.htmlFor);if(!f)f=lab.querySelector('input,textarea,select');if(!f&&lab.parentElement)f=lab.parentElement.querySelector('input,textarea,select')}catch(_){}if(f&&visible(f))return f}
    return null;
  }
  function textOf(el){return norm([el.innerText,el.textContent,el.value,el.name,el.id,el.placeholder,el.getAttribute&&el.getAttribute('aria-label')].filter(Boolean).join(' '))}
  function setField(el,val){if(!el||val===undefined||val===null||val==='')return false;try{el.focus();if(el.tagName==='SELECT'){const nv=norm(val),opts=[...el.options],o=opts.find(x=>norm(x.value)===nv||norm(x.textContent).includes(nv));if(!o)return false;el.value=o.value}else el.value=val;fire(el);return true}catch(_){return false}}

  function setExact(selector,val){const el=document.querySelector(selector);if(!el)return false;return setField(el,val)}
  function setSelectExact(selector,value,text=''){
    let el=null;try{el=document.querySelector(selector)}catch(_){}
    if(!el||el.tagName!=='SELECT')return false;
    try{
      el.disabled=false;
      const wanted=String(value==null?'':value).trim();
      if(!wanted)return false;
      let opt=[...el.options].find(o=>String(o.value)===wanted);
      if(!opt&&text){opt=new Option(String(text),wanted,true,true);el.add(opt)}
      if(!opt){
        const n=norm(wanted);
        opt=[...el.options].find(o=>norm(o.textContent)===n||norm(o.textContent).includes(n));
      }
      if(!opt)return false;
      el.focus();el.value=opt.value;fire(el);
      return String(el.value)===String(opt.value);
    }catch(_){return false}
  }
  async function waitSelectEnabled(selector,timeout=10000){
    const end=Date.now()+timeout;
    while(Date.now()<end){
      const el=document.querySelector(selector);
      if(el&&!el.disabled&&el.getAttribute('aria-disabled')!=='true')return el;
      await sleep(250);
    }
    return document.querySelector(selector);
  }
  function knownMunicipioIbge(nome,uf){
    const k=norm(nome)+'|'+String(uf||'').toUpperCase();
    const map={'rio de janeiro|RJ':'3304557','nova iguacu|RJ':'3303500'};
    return map[k]||'';
  }
  function serviceMeta(s){
    const code=String((s&&s.codigo)||'').trim().replace(/\.000$/,'');
    const known={
      '01.07.01':{
        ctnText:'01.07.01 - Suporte técnico em informática, inclusive instalação, configuração e manutenção de programas de computação e bancos de dados.',
        nbs:'115013000',
        nbsText:'115013000 - Serviços de suporte em tecnologia da informação (TI)'
      },
      '14.01.01':{
        ctnText:'14.01.01',
        nbs:'120018900',
        nbsText:'120018900'
      }
    };
    const m=known[code]||{};
    return {
      code,
      ctnText:String((s&&s.codigo_texto)||m.ctnText||code),
      nbs:String((s&&s.nbs_codigo)||m.nbs||''),
      nbsText:String((s&&s.nbs_texto)||m.nbsText||((s&&s.nbs_codigo)||m.nbs||'')),
      municipio:String((s&&s.municipio)||((payload&&payload.tomador&&payload.tomador.municipio)||'')),
      uf:String((s&&s.uf)||((payload&&payload.tomador&&payload.tomador.uf)||'')).toUpperCase(),
      ibge:String((s&&s.municipio_ibge)||knownMunicipioIbge((s&&s.municipio)||((payload&&payload.tomador&&payload.tomador.municipio)||''),(s&&s.uf)||((payload&&payload.tomador&&payload.tomador.uf)||'')))
    };
  }
  function radioGroups(){
    const groups=new Map();
    for(const r of [...document.querySelectorAll('input[type=radio]')]){
      if(!r.name)continue;if(!groups.has(r.name))groups.set(r.name,[]);groups.get(r.name).push(r)
    }
    return groups;
  }
  function radioLabel(r){
    try{
      const l=r.id?document.querySelector('label[for="'+CSS.escape(r.id)+'"]'):null;
      if(l)return norm(l.textContent);
      if(r.parentElement)return norm(r.parentElement.textContent);
    }catch(_){}
    return '';
  }
  function groupContext(radios){
    if(!radios||!radios.length)return '';
    let n=radios[0];
    for(let i=0;i<7&&n;i++,n=n.parentElement){
      const t=norm(n.textContent||'');
      if(t&&t.length<1800)return t;
    }
    return '';
  }
  function clickRadioNear(questionParts,answerParts=['não','nao']){
    const q=questionParts.map(norm), a=answerParts.map(norm);
    for(const [name,radios] of radioGroups()){
      let context='';
      for(const r of radios){
        let n=r;
        for(let i=0;i<7&&n;i++,n=n.parentElement){
          const t=norm(n.textContent||'');
          if(q.every(x=>t.includes(x))){context=t;break}
        }
        if(context)break;
      }
      if(!context)continue;
      const target=radios.find(r=>a.some(x=>{const l=radioLabel(r);return l===x||l.startsWith(x+' ')||l.includes(' '+x+' ')}));
      if(target&&!target.disabled){
        try{if(!target.checked){const lab=target.id?document.querySelector('label[for="'+CSS.escape(target.id)+'"]'):null;(lab&&visible(lab)?lab:target).click()}return !!target.checked}catch(_){}
      }
    }
    return false;
  }
  function clickRadio(name,value){
    let el=null;try{el=document.querySelector(`input[type="radio"][name="${name}"][value="${value}"]`)}catch(_){}
    if(!el||el.disabled)return false;
    try{
      const lab=el.id?document.querySelector('label[for="'+CSS.escape(el.id)+'"]'):null;
      if(lab&&visible(lab)){lab.focus&&lab.focus();lab.click()}else{el.focus();el.click()}
      return !!el.checked;
    }catch(_){return false}
  }
  function clickLabeledRadio(texts){
    const wanted=texts.map(norm);
    for(const lab of [...document.querySelectorAll('label')].filter(visible)){
      const t=norm(lab.textContent);
      if(!wanted.some(w=>t===w||t.includes(w)))continue;
      try{
        let el=null;if(lab.htmlFor)el=document.getElementById(lab.htmlFor);if(!el)el=lab.querySelector('input[type=radio]');
        if(el&&el.disabled)continue;
        lab.focus&&lab.focus();lab.click();
        return true;
      }catch(_){}
    }
    return false;
  }
  function isEnabled(selector){try{const el=document.querySelector(selector);return !!el&&!el.disabled&&el.getAttribute('aria-disabled')!=='true'}catch(_){return false}}
  async function waitEnabled(selectors,timeout=15000){
    const end=Date.now()+timeout, list=Array.isArray(selectors)?selectors:[selectors];
    while(Date.now()<end){if(list.every(isEnabled))return true;await sleep(250)}
    return false;
  }
  function digits(v){return String(v||'').replace(/\D/g,'')}
  function clickText(texts,selector='button,a,label,[role=button],[role=radio],input[type=radio]'){const wanted=texts.map(norm),els=[...document.querySelectorAll(selector)].filter(visible),el=els.find(e=>wanted.some(w=>textOf(e)===w||textOf(e).includes(w)));if(!el)return false;try{el.focus();el.click();return true}catch(_){return false}}
  function elementSummary(el){
    if(!el)return '(não localizado)';
    try{
      const parts=[];
      const tag=String(el.tagName||'').toLowerCase(); if(tag)parts.push(tag);
      if(el.id)parts.push('#'+el.id);
      if(el.name)parts.push('name='+el.name);
      if(el.type)parts.push('type='+el.type);
      const txt=String(el.innerText||el.textContent||el.value||'').replace(/\s+/g,' ').trim();
      if(txt)parts.push('texto="'+txt.slice(0,90)+'"');
      return parts.join(' | ')||'(controle sem identificação)';
    }catch(_){return '(não foi possível descrever o controle)'}
  }
  function clickAdvance(){
    const els=[...document.querySelectorAll('button,a,input[type=submit],input[type=button]')].filter(el=>visible(el)&&!el.disabled&&el.getAttribute('aria-disabled')!=='true');
    const exact=els.find(e=>{const t=norm(e.innerText||e.textContent||e.value||e.getAttribute('aria-label')||'');return t==='avancar'||t==='avançar'||t==='proximo'||t==='próximo'});
    const el=exact||els.find(e=>{const t=norm(e.innerText||e.textContent||e.value||e.getAttribute('aria-label')||'');return t.includes('avancar')||t.includes('proximo')});
    if(!el)return {ok:false,detail:'Nenhum botão Avançar/Próximo visível e habilitado foi localizado.'};
    try{el.focus();el.click();return {ok:true,detail:elementSummary(el)}}catch(err){return {ok:false,detail:'Falha ao clicar: '+String(err&&err.message?err.message:err)}}
  }
  function labelForField(el){
    try{
      if(el.id){const lab=document.querySelector('label[for="'+CSS.escape(el.id)+'"]');if(lab&&lab.textContent.trim())return lab.textContent.replace(/\s+/g,' ').trim()}
      const wrap=el.closest('label,.form-group,.mb-3,.row,div');
      if(wrap){const lab=wrap.querySelector('label');if(lab&&lab.textContent.trim())return lab.textContent.replace(/\s+/g,' ').trim()}
    }catch(_){}
    return el.name||el.id||el.placeholder||el.getAttribute('aria-label')||'campo';
  }
  function collectValidationDiagnostics(){
    const out=[]; const seen=new Set();
    const push=(x)=>{x=String(x||'').replace(/\s+/g,' ').trim();if(x&&!seen.has(x)){seen.add(x);out.push(x)}};
    try{
      const selectors=['[role=alert]','.validation-summary-errors','.field-validation-error','.validation-message','.invalid-feedback','.text-danger','.alert-danger','.alert-warning','.erro','.error'];
      for(const el of document.querySelectorAll(selectors.join(','))){if(visible(el))push(el.textContent)}
    }catch(_){}
    try{
      const fields=[...document.querySelectorAll('input,select,textarea')].filter(visible);
      for(const el of fields){
        let invalid=false;
        try{invalid=el.getAttribute('aria-invalid')==='true'||(typeof el.checkValidity==='function'&&!el.checkValidity())}catch(_){}
        const required=el.required||el.getAttribute('aria-required')==='true';
        const empty=el.type==='checkbox'||el.type==='radio'?!el.checked:String(el.value||'').trim()==='';
        if(invalid||(required&&empty)){
          const name=labelForField(el);
          let why='';try{why=el.validationMessage||''}catch(_){}
          push(name+(why?' — '+why:' — obrigatório/não validado'));
        }
      }
    }catch(_){}
    if(!out.length){
      try{
        const txt=[...document.querySelectorAll('small,span,p,div')].filter(visible).map(e=>String(e.textContent||'').replace(/\s+/g,' ').trim()).filter(t=>/obrigat|informe|inv[aá]lid|erro|necess[aá]rio|selecione|preencha/i.test(t)&&t.length<240);
        txt.slice(0,8).forEach(push);
      }catch(_){}
    }
    return out.slice(0,12);
  }
  async function confirmStageChanged(fromStage, retryCheckpoint, title){
    for(let i=0;i<10;i++){await sleep(500);const now=detectStage();if(now!==fromStage&&now!=='desconhecida'){addDebugStep(title+' - navegação confirmada',`Nova etapa detectada: ${now}.`);return true}}
    const diags=collectValidationDiagnostics();
    setCheckpoint(retryCheckpoint);
    const detail=diags.length?('O portal permaneceu na mesma tela. Validações encontradas: '+diags.join(' | ')):'O portal permaneceu na mesma tela após o clique. Nenhuma mensagem de validação visível foi encontrada; confira os campos destacados pelo próprio portal.';
    pauseWithError(title+' - portal não avançou',detail);
    return false;
  }
  function fieldResult(name,ok,missing){addDebugStep(name,ok?'OK':('Não localizado'+(missing?': '+missing:'')))}
  function pauseWithError(title,detail){setPaused(true);addDebugStep(title,detail);updateBox('Automação pausada.',detail)}

  async function pessoas(){
    if(getCheckpoint()==='pessoas_avancando')return;
    setCheckpoint('pessoas_preenchendo');
    addDebugStep('Pessoas','Ordem segura: IBS/CBS = Não → competência → emitente Prestador/Fornecedor → tomador.');
    updateBox('Preenchendo Pessoas…','Primeiro Informações Gerais e emitente; depois o tomador.');

    const t=payload.tomador||{}, missing=[];

    // 1) Gate obrigatório da reforma IBS/CBS 2026. O portal mantém o restante do
    // formulário bloqueado enquanto esta pergunta não for respondida.
    const ibsOk=clickRadio('PreencherInfoIBSCBS','0') || clickLabeledRadio(['não','nao']);
    fieldResult('IBS/CBS',ibsOk,'Opção “Não”');
    if(!ibsOk){pauseWithError('Pessoas - IBS/CBS não localizado','Não encontrei a opção “Não” de “Preencher as informações IBS/CBS?”.');return}
    await sleep(250);

    // 2) Competência. O Organiza envia YYYY-MM-DD e o campo oficial é DataCompetencia.
    const iso=String(payload.competencia||'').trim();
    const comp=document.querySelector('#DataCompetencia')||findField(['competencia']);
    if(!comp){pauseWithError('Pessoas - competência não localizada','Não encontrei o campo Data de Competência (#DataCompetencia).');return}
    if(!setField(comp,comp.type==='date'?iso:(iso&&iso.includes('-')?iso.split('-').reverse().join('/'):iso))){
      pauseWithError('Pessoas - competência não preenchida','O campo foi localizado, mas não aceitou a competência enviada pelo Organiza.');return
    }
    fieldResult('Data de Competência',true,iso);
    await sleep(500);

    // Alguns ciclos AJAX do portal reconstroem os radios após a competência.
    clickRadio('PreencherInfoIBSCBS','0');

    // Aguarda o portal liberar os blocos abaixo antes de qualquer tentativa de tomador.
    const liberado=await waitEnabled(['#DataCompetencia','input[name="Tomador.LocalDomicilio"][value="1"]'],15000);
    if(!liberado){
      setCheckpoint('pessoas_revisao');
      pauseWithError('Pessoas - formulário ainda bloqueado','IBS/CBS e competência foram informados, mas o portal não liberou o bloco do tomador em até 15 segundos.');
      return
    }
    addDebugStep('Pessoas - formulário liberado','IBS/CBS = Não e competência aceitos pelo portal.');

    // 3) Emitente: é SEMPRE quem está logado no portal (Karaokê RJ/Débora).
    // Apenas reafirmamos Prestador/Fornecedor. Nunca escrevemos CNPJ, Razão Social,
    // endereço ou município do emitente com dados do tomador.
    const prestadorOk=clickLabeledRadio(['prestador/fornecedor','prestador']);
    fieldResult('Emitente',prestadorOk,'Prestador/Fornecedor');
    if(!prestadorOk)missing.push('Emitente Prestador/Fornecedor');
    await sleep(600);
    addDebugStep('Emitente preservado','CNPJ/Razão Social/endereço do emitente ficam sob controle do próprio portal; dados do tomador não são escritos nesse bloco.');

    // 4) Tomador brasileiro. Usa os IDs oficiais do portal para nunca acertar campos
    // do emitente por engano.
    const brasilOk=clickRadio('Tomador.LocalDomicilio','1');
    fieldResult('Tomador - Brasil',brasilOk,'Tomador.LocalDomicilio=1');
    if(!brasilOk){setCheckpoint('pessoas_revisao');pauseWithError('Pessoas - tomador Brasil não localizado','Não encontrei a opção Brasil do Tomador do Serviço.');return}
    await sleep(450);

    const doc=digits(t.documento);
    const inscr=document.querySelector('#Tomador_Inscricao');
    if(inscr&&doc){setField(inscr,doc);await sleep(1200)}else missing.push('CPF/CNPJ do tomador');

    const nome=document.querySelector('#Tomador_Nome');
    if(nome&&t.nome&&!nome.disabled&&!nome.readOnly)setField(nome,t.nome);
    else if(t.nome&&!nome)missing.push('Nome/Razão Social do tomador');

    const tel=document.querySelector('#Tomador_Telefone');
    if(tel&&t.telefone&&!tel.disabled&&!tel.readOnly)setField(tel,digits(t.telefone));
    const email=document.querySelector('#Tomador_Email');
    if(email&&t.email&&!email.disabled&&!email.readOnly)setField(email,t.email);

    // O portal consulta RFB/CNC pelo CPF/CNPJ brasileiro e pode completar os demais
    // dados cadastrais; não forçamos CEP/endereço por seletores genéricos.
    const interOk=clickRadio('Intermediario.LocalDomicilio','0');
    if(!interOk)addDebugStep('Intermediário','Opção “não informado” não localizada; será conferida pela validação do portal.');

    fieldResult('Pessoas - campos',missing.length===0,missing.join(', '));
    if(missing.some(x=>x.includes('CPF/CNPJ')||x.includes('Emitente'))){setCheckpoint('pessoas_revisao');pauseWithError('Pessoas - campo obrigatório não localizado',missing.join(', '));return}

    await sleep(700);
    setCheckpoint('pessoas_avancando');
    const advance=clickAdvance();
    if(advance.ok){
      addDebugStep('Pessoas - Avançar acionado',advance.detail+' | aguardando confirmação da mudança de etapa.');
      updateBox('Pessoas preenchido.','Confirmando se o portal aceitou e abriu Serviço…');
      await confirmStageChanged('pessoas','pessoas_revisao','Pessoas');
    }else{
      setCheckpoint('pessoas_revisao');
      pauseWithError('Pessoas - Avançar não localizado',advance.detail+' Confira a tela. Depois clique em Continuar automação para tentar novamente.');
    }
  }

  async function servico(){
    if(getCheckpoint()==='servico_avancando')return;
    setCheckpoint('servico_preenchendo');
    addDebugStep('Serviço','Ordem segura: País Brasil → Município da prestação → CTN → operação normal (Não) → NBS → descrição.');
    updateBox('Preenchendo Serviço…','Município, código de tributação, NBS e descrição.');

    const s=payload.servico||{}, meta=serviceMeta(s), missing=[];

    // País Brasil.
    const paisOk=setSelectExact('#LocalPrestacao_CodigoPaisPrestacao','BR','Brasil');
    fieldResult('Serviço - País',paisOk,'Brasil');
    await sleep(450);

    // Município da prestação: usa o código IBGE vindo do Organiza/CEP. Quando o
    // cadastro não trouxe IBGE, há fallback apenas para os municípios já validados.
    const munSel=await waitSelectEnabled('#LocalPrestacao_CodigoMunicipioPrestacao',7000);
    let munOk=false;
    if(munSel&&meta.ibge){munOk=setSelectExact('#LocalPrestacao_CodigoMunicipioPrestacao',meta.ibge,`${meta.municipio}/${meta.uf}`)}
    if(!munOk)missing.push('Município da prestação (IBGE)');
    fieldResult('Serviço - Município',munOk,`${meta.municipio}/${meta.uf} | IBGE ${meta.ibge||'não informado'}`);
    await sleep(900);

    // Código de Tributação Nacional.
    const ctnSel=await waitSelectEnabled('#ServicoPrestado_CodigoTributacaoNacional',9000);
    const ctnOk=!!ctnSel&&!!meta.code&&setSelectExact('#ServicoPrestado_CodigoTributacaoNacional',meta.code,meta.ctnText);
    if(!ctnOk)missing.push('Código de Tributação Nacional');
    fieldResult('Serviço - CTN',ctnOk,meta.code||'não informado');
    await sleep(900);

    // Operação normal: não é imunidade, exportação nem não incidência.
    const normalOk=clickRadio('ServicoPrestado.HaExportacaoImunidadeNaoIncidencia','0') || clickRadioNear(['imunidade','exportacao','nao incidencia'],['não','nao']);
    if(!normalOk)missing.push('Imunidade/exportação/não incidência = Não');
    fieldResult('Serviço - Operação normal',normalOk,'Não');
    await sleep(450);

    // NBS correspondente.
    const nbsSel=await waitSelectEnabled('#ServicoPrestado_CodigoNBS',9000);
    const nbsOk=!!nbsSel&&!!meta.nbs&&setSelectExact('#ServicoPrestado_CodigoNBS',meta.nbs,meta.nbsText);
    if(!nbsOk)missing.push('Item da NBS');
    fieldResult('Serviço - NBS',nbsOk,meta.nbs||'não informado');

    // Descrição editável vinda do Organiza.
    const desc=document.querySelector('#ServicoPrestado_Descricao')||findField(['descricao'],'textarea,input');
    const descOk=!!desc&&!!String(s.descricao||'').trim()&&setField(desc,s.descricao);
    if(!descOk)missing.push('Descrição do serviço');
    fieldResult('Serviço - Descrição',descOk,String(s.descricao||'').slice(0,100));

    if(missing.length){
      setCheckpoint('servico_revisao');
      pauseWithError('Serviço - preenchimento incompleto',missing.join(' | '));
      return
    }

    await sleep(700);
    const diags=collectValidationDiagnostics();
    if(diags.length&&diags.some(x=>/campo obrigat|obrigat[oó]rio/i.test(x))){
      addDebugStep('Serviço - validação antes de avançar',diags.join(' | '));
    }
    setCheckpoint('servico_avancando');
    const advance=clickAdvance();
    if(advance.ok){
      addDebugStep('Serviço - Avançar acionado',advance.detail+' | aguardando confirmação da mudança de etapa.');
      updateBox('Serviço preenchido.','Confirmando se o portal aceitou e abriu Valores…');
      await confirmStageChanged('servico','servico_revisao','Serviço');
    }else{
      setCheckpoint('servico_revisao');
      pauseWithError('Serviço - Avançar não localizado',advance.detail+' Confira Município, CTN, operação normal, NBS e descrição; depois clique em Continuar automação.')
    }
  }

  async function valores(){
    if(getCheckpoint()==='valores_avancando')return;
    setCheckpoint('valores_preenchendo');
    addDebugStep('Valores','Valor total + tributação padrão validada da Karaokê RJ/MEI.');
    updateBox('Preenchendo Valores…','Valor total e respostas fiscais padrão; campos bloqueados pelo Simples Nacional são preservados.');

    const s=payload.servico||{}, missing=[];
    const val=document.querySelector('#Valores_ValorServico')||findField(['valor','servico'])||findField(['valor']);
    if(!val){setCheckpoint('valores_revisao');pauseWithError('Valores - campo não localizado','Não encontrei #Valores_ValorServico.');return}
    const br=Number(s.valor||0).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
    const valOk=setField(val,br);fieldResult('Valor do serviço',valOk,br);if(!valOk)missing.push('Valor do serviço');
    await sleep(350);

    // Os campos PIS/COFINS e IBS/CBS aparecem bloqueados para o MEI/Simples e devem
    // permanecer sob controle do portal. As respostas abaixo reproduzem a nota
    // validada enviada pelo usuário.
    const susp=clickRadioNear(['exigibilidade','suspensa'],['não','nao']);
    const ret=clickRadioNear(['retencao','issqn'],['não','nao']);
    const ben=clickRadioNear(['beneficio','municipal'],['não','nao']);
    const ded=clickRadioNear(['deducao','reducao'],['não','nao']);
    fieldResult('ISSQN - exigibilidade suspensa',susp,'Não');
    fieldResult('ISSQN - retenção',ret,'Não');
    fieldResult('ISSQN - benefício municipal',ben,'Não');
    fieldResult('ISSQN - dedução/redução',ded,'Não');
    if(!susp)missing.push('Exigibilidade suspensa = Não');
    if(!ret)missing.push('Retenção ISSQN = Não');
    if(!ben)missing.push('Benefício municipal = Não');
    if(!ded)missing.push('Dedução/Redução = Não');

    // Valor aproximado dos tributos: opção usada na nota validada.
    let tribApprox=clickLabeledRadio(['não informar nenhum valor estimado para os tributos','nao informar nenhum valor estimado para os tributos']);
    if(!tribApprox)tribApprox=clickRadioNear(['valor aproximado','tributos'],['não informar','nao informar']);
    fieldResult('Valor aproximado dos tributos',tribApprox,'Não informar nenhum valor estimado');
    if(!tribApprox)missing.push('Valor aproximado dos tributos');

    if(missing.length){
      setCheckpoint('valores_revisao');
      pauseWithError('Valores - preenchimento incompleto',missing.join(' | '));
      return
    }

    await sleep(700);
    setCheckpoint('valores_avancando');
    const advance=clickAdvance();
    if(advance.ok){
      addDebugStep('Valores - Avançar acionado',advance.detail+' | aguardando a tela final de conferência.');
      updateBox('Valores preenchidos.','A extensão vai parar assim que sair de Valores; nunca clicará em Emitir NFS-e.');
      const changed=await confirmStageChanged('valores','valores_revisao','Valores');
      if(changed&&detectStage()!=='valores'){
        markReady();
      }
    }else{
      setCheckpoint('valores_revisao');
      pauseWithError('Valores - Avançar não localizado',advance.detail+' Confira os campos obrigatórios e clique em Continuar automação.')
    }
  }

  function markReady(){
    if(getCheckpoint()!=='rascunho_pronto'){setCheckpoint('rascunho_pronto');setActive(false);setPaused(false);addDebugStep('Rascunho pronto para conferência.','AUTOMAÇÃO ENCERRADA. A extensão não clicará em Emitir NFS-e.');updateBox('Rascunho pronto para conferência.','Pare aqui. Confira os dados e emita manualmente quando desejar.')}
    if(!sessionStorage.getItem(SESSION_READY_SENT)){sessionStorage.setItem(SESSION_READY_SENT,'1');try{chrome.runtime.sendMessage({type:'NFSE_DRAFT_READY',rascunhoId:payload.rascunho_id||null})}catch(_){}}
  }

  async function run(force=false){
    if(!payload||running||!getActive()||getPaused()||getCheckpoint()==='rascunho_pronto')return;
    running=true;
    try{
      const stage=detectStage(), cp=getCheckpoint();
      if(stage==='revisao'){markReady();return}
      if(stage==='pessoas'){
        if(cp==='pessoas_avancando'){
          const diags=collectValidationDiagnostics();
          setCheckpoint('pessoas_revisao');
          pauseWithError('Pessoas - navegação não concluída',diags.length?('O portal continuou em Pessoas. '+diags.join(' | ')):'O portal continuou em Pessoas após o Avançar. Confira os campos destacados e clique em Continuar automação para tentar novamente.');
          return
        }
        if(cp.startsWith('servico_')||cp.startsWith('valores_')){pauseWithError('Proteção anti-retorno',`Tela Pessoas reapareceu, mas o fluxo já estava em ${cp}. Nenhuma ação foi executada.`);return}
        await pessoas();return;
      }
      if(stage==='servico'){
        if(cp==='servico_avancando'){
          const diags=collectValidationDiagnostics();
          setCheckpoint('servico_revisao');
          pauseWithError('Serviço - navegação não concluída',diags.length?('O portal continuou em Serviço. '+diags.join(' | ')):'O portal continuou em Serviço após o Avançar. Confira os campos destacados e clique em Continuar automação.');
          return
        }
        await servico();return;
      }
      if(stage==='valores'){
        if(cp==='valores_avancando'){
          const diags=collectValidationDiagnostics();
          setCheckpoint('valores_revisao');
          pauseWithError('Valores - navegação não concluída',diags.length?('O portal continuou em Valores. '+diags.join(' | ')):'O portal continuou em Valores após o Avançar. Confira os campos destacados e clique em Continuar automação.');
          return
        }
        await valores();return;
      }
      addDebugStep('Tela não reconhecida.','Nenhuma ação automática foi executada nesta página.');updateBox('Aguardando uma etapa da DPS…','Nenhuma ação será repetida enquanto a tela não for reconhecida.');
    }catch(err){pauseWithError('Erro na automação',String(err&&err.message?err.message:err))}
    finally{running=false}
  }
  function scheduleRun(ms=350,force=false){if(runTimer)clearTimeout(runTimer);runTimer=setTimeout(()=>{runTimer=null;run(force)},ms)}

  function accept(p){
    payload=p;const fp=fingerprint(p),oldFp=sessionStorage.getItem(SESSION_FP)||'';payloadFp=fp;
    try{sessionStorage.setItem(SESSION_PAYLOAD,JSON.stringify(p));sessionStorage.setItem(SESSION_FP,fp)}catch(_){}
    if(oldFp!==fp){setActive(true);setPaused(false);setCheckpoint('inicio');try{sessionStorage.removeItem(SESSION_READY_SENT)}catch(_){}clearDebugSteps();addDebugStep('Dados do Organiza recebidos.','Novo rascunho identificado. Automação iniciada com proteção contra repetição.')}
    else addDebugStep('Dados do Organiza recuperados.','Mantendo checkpoint existente; a automação não será reiniciada do zero.');
    updateBox();scheduleRun(300,true);
  }

  try{const x=JSON.parse(sessionStorage.getItem(SESSION_PAYLOAD)||'null');if(x){payload=x;payloadFp=sessionStorage.getItem(SESSION_FP)||fingerprint(x);updateBox('Dados da NFS-e recuperados.','Mantendo o checkpoint da navegação atual.')}}catch(_){}
  chrome.runtime.onMessage.addListener(msg=>{if(msg&&msg.type==='NFSE_PAYLOAD'&&msg.payload)accept(msg.payload)});
  chrome.runtime.sendMessage({type:'NFSE_GET_PAYLOAD'}).then(a=>{if(a&&a.payload)accept(a.payload)}).catch(()=>{});
  new MutationObserver(()=>{if(payload&&getActive()&&!getPaused())scheduleRun(500)}).observe(document.documentElement,{subtree:true,childList:true});
  window.addEventListener('popstate',()=>{if(payload)scheduleRun(350,true)});
  setInterval(()=>{if(payload&&getActive()&&!getPaused())scheduleRun(100)},2500);
  if(payload)scheduleRun(500,true);
})();
