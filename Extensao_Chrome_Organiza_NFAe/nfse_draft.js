(function(){
  'use strict';
  const BOX='organiza-nfse-draft-box';
  let payload=null, running=false, lastStep='';
  const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
  const norm=(v)=>String(v||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/\s+/g,' ').trim();
  const visible=(el)=>{try{const s=getComputedStyle(el);return s.display!=='none'&&s.visibility!=='hidden'&&!!(el.offsetWidth||el.offsetHeight||el.getClientRects().length)}catch(_){return true}};
  const fire=(el)=>{['input','change','blur'].forEach(t=>{try{el.dispatchEvent(new Event(t,{bubbles:true}))}catch(_){}})};
  function box(msg,detail=''){
    let el=document.getElementById(BOX);
    if(!el){el=document.createElement('div');el.id=BOX;el.style.cssText='position:fixed;right:14px;bottom:14px;z-index:2147483647;width:360px;max-width:calc(100vw - 28px);background:#fff;border:2px solid #15803d;border-radius:10px;padding:12px;font:13px Arial;color:#172033;box-shadow:0 12px 32px #0003';document.body.appendChild(el);}
    el.innerHTML='<strong>Organiza → NFS-e</strong><div style="margin-top:6px">'+msg+'</div>'+(detail?'<small style="display:block;margin-top:5px;color:#64748b">'+detail+'</small>':'')+'<div style="margin-top:8px;font-weight:bold;color:#b45309">A extensão nunca clica em “Emitir NFS-e”.</div>';
  }
  function candidates(){return [...document.querySelectorAll('input,textarea,select,button,a,[role=button],[role=radio],label')].filter(visible)}
  function textOf(el){return norm([el.innerText,el.textContent,el.value,el.name,el.id,el.placeholder,el.getAttribute&&el.getAttribute('aria-label')].filter(Boolean).join(' '))}
  function findField(words,selector='input,textarea,select'){
    const wants=words.map(norm);
    const els=[...document.querySelectorAll(selector)].filter(visible);
    for(const el of els){
      const own=textOf(el); if(wants.every(w=>own.includes(w))) return el;
      let label='';
      try{if(el.id){const l=document.querySelector('label[for="'+CSS.escape(el.id)+'"]');if(l)label=textOf(l)}}catch(_){ }
      if(!label){try{const l=el.closest('label');if(l)label=textOf(l)}catch(_){ }}
      if(wants.every(w=>(own+' '+label).includes(w))) return el;
    }
    // label nearby fallback
    for(const lab of [...document.querySelectorAll('label,span,div,p')].filter(visible)){
      const lt=norm(lab.textContent); if(!wants.every(w=>lt.includes(w))) continue;
      let f=null; try{if(lab.htmlFor)f=document.getElementById(lab.htmlFor); if(!f)f=lab.querySelector('input,textarea,select'); if(!f&&lab.parentElement)f=lab.parentElement.querySelector('input,textarea,select')}catch(_){ }
      if(f&&visible(f)) return f;
    }
    return null;
  }
  function setField(el,val){if(!el||val===undefined||val===null||val==='')return false; try{el.focus(); if(el.tagName==='SELECT'){const nv=norm(val);const opts=[...el.options];const o=opts.find(x=>norm(x.value)===nv||norm(x.textContent).includes(nv));if(o)el.value=o.value;else return false;}else{el.value=val;} fire(el);return true}catch(_){return false}}
  function clickText(texts, selector='button,a,label,[role=button],[role=radio],input[type=radio]'){
    const wanted=texts.map(norm); const els=[...document.querySelectorAll(selector)].filter(visible);
    const el=els.find(e=>wanted.some(w=>textOf(e)===w||textOf(e).includes(w)));
    if(!el)return false; try{el.focus();el.click();return true}catch(_){return false}
  }
  function clickAdvance(){
    const els=[...document.querySelectorAll('button,a,input[type=submit],input[type=button]')].filter(visible);
    const el=els.find(e=>{const t=textOf(e);return t==='avancar'||t.includes('avancar')||t==='proximo'||t.includes('proximo')});
    if(!el)return false; try{el.focus();el.click();return true}catch(_){return false}
  }
  async function pessoas(){
    const t=payload.tomador||{};
    box('Preenchendo Pessoas…','Competência e tomador.');
    let comp=findField(['competencia']);
    if(comp){const iso=payload.competencia||'';const br=iso&&iso.includes('-')?iso.split('-').reverse().join('/'):iso;setField(comp,comp.type==='date'?iso:br)}
    clickText(['prestador']);
    // primeiro grupo "Brasil" disponível costuma ser o Tomador; prefere label/contexto com tomador
    const radios=[...document.querySelectorAll('input[type=radio],label,[role=radio]')].filter(visible);
    let brasil=radios.find(e=>textOf(e).includes('brasil')&&norm((e.closest('section,fieldset,div')||{}).textContent).includes('tomador'));
    if(brasil){try{brasil.click()}catch(_){}} else clickText(['brasil']);
    await sleep(350);
    const doc=t.documento||'';
    let df=findField([t.tipo_documento==='CNPJ'?'cnpj':'cpf']);
    if(!df) df=findField(['cpf']); if(!df) df=findField(['cnpj']);
    setField(df,doc); await sleep(900);
    setField(findField(['nome']),t.nome);
    setField(findField(['cep']),t.cep);
    setField(findField(['logradouro']),t.logradouro);
    setField(findField(['numero']),t.numero);
    setField(findField(['complemento']),t.complemento);
    setField(findField(['bairro']),t.bairro);
    setField(findField(['email']),t.email);
    setField(findField(['telefone']),t.telefone);
    // Intermediário não informado, quando visível
    const inter=[...document.querySelectorAll('label,[role=radio]')].filter(visible).find(e=>textOf(e).includes('intermediario nao informado'));
    if(inter)try{inter.click()}catch(_){ }
    await sleep(500);
    if(clickAdvance()){lastStep='pessoas';box('Pessoas preenchido.','Avançando para Serviço…');}
    else box('Pessoas preenchido.','Não encontrei “Avançar”. Confira a tela e avance manualmente; a extensão continuará na próxima etapa.');
  }
  async function servico(){
    const s=payload.servico||{}; box('Preenchendo Serviço…','Código, descrição e local da prestação.');
    let cod=findField(['codigo','tributacao']); if(!cod)cod=findField(['codigo','servico']);
    if(cod){
      if(cod.tagName==='SELECT') setField(cod,s.codigo); else {setField(cod,s.codigo); try{cod.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));cod.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))}catch(_){}}
      await sleep(900);
    }
    let desc=findField(['descricao'],'textarea,input'); if(!desc) desc=[...document.querySelectorAll('textarea')].find(visible); setField(desc,s.descricao);
    let mun=findField(['municipio','prestacao']); if(mun){setField(mun,s.municipio); await sleep(500); try{mun.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));mun.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))}catch(_){}}
    await sleep(500);
    if(clickAdvance()){lastStep='servico';box('Serviço preenchido.','Avançando para Valores…');}
    else box('Serviço preenchido.','Confira o código/município e avance manualmente se necessário.');
  }
  async function valores(){
    const s=payload.servico||{}; box('Preenchendo Valores…','Somente o valor total da NFS-e.');
    let val=findField(['valor','servico']); if(!val) val=findField(['valor']);
    const br=Number(s.valor||0).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2}); setField(val,br);
    await sleep(500);
    if(clickAdvance()){lastStep='valores';box('Rascunho preparado.','Chegando à revisão. A emissão final ficará para você.');}
    else box('Valor preenchido.','Avance manualmente para a revisão. A extensão não emitirá a nota.');
  }
  async function run(){
    if(!payload||running)return; running=true;
    try{
      const path=norm(location.pathname);
      const body=norm(document.body&&document.body.innerText);
      if(body.includes('emitir nfs-e')&&(body.includes('revise sua')||body.includes('revisao da declaracao')||path.includes('revis'))){
        box('Rascunho pronto para conferência.','Pare aqui. Confira os dados e emita manualmente quando desejar.');
        if(!sessionStorage.getItem('organiza_nfse_ready_notified')){
          sessionStorage.setItem('organiza_nfse_ready_notified','1');
          try{chrome.runtime.sendMessage({type:'NFSE_DRAFT_READY',rascunhoId:payload.rascunho_id||null})}catch(_){ }
        }
        return;
      }
      if(path.includes('/dps/pessoas')||body.includes('tomador do servico')) await pessoas();
      else if(path.includes('/dps/servico')||body.includes('servico prestado')) await servico();
      else if(path.includes('/dps/tributacao')||path.includes('/dps/valores')||body.includes('valor do servico')) await valores();
      else box('Aguardando uma etapa da DPS…','Abra Nova NFS-e / Pessoas.');
    }finally{running=false}
  }
  function accept(p){payload=p;sessionStorage.removeItem('organiza_nfse_ready_notified');sessionStorage.setItem('organiza_nfse_payload',JSON.stringify(p));box('Dados do Organiza recebidos.','A preparação do rascunho começou.');setTimeout(run,250)}
  try{const x=JSON.parse(sessionStorage.getItem('organiza_nfse_payload')||'null');if(x)payload=x}catch(_){ }
  chrome.runtime.onMessage.addListener(msg=>{if(msg&&msg.type==='NFSE_PAYLOAD'&&msg.payload)accept(msg.payload)});
  chrome.runtime.sendMessage({type:'NFSE_GET_PAYLOAD'}).then(a=>{if(a&&a.payload)accept(a.payload)}).catch(()=>{});
  new MutationObserver(()=>{if(payload)setTimeout(run,350)}).observe(document.documentElement,{subtree:true,childList:true});
  window.addEventListener('popstate',()=>setTimeout(run,300));
  setInterval(()=>{if(payload)run()},1800);
})();
