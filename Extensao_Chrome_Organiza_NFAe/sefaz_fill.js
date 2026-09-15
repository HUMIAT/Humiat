(function(){
  const STORAGE_KEY = 'organiza_nfae_payload';
  const SAVED_AT_KEY = 'organiza_nfae_saved_at';
  const MAX_AGE_MS = 4 * 60 * 60 * 1000;
  let payload = null;
  let filling = false;
  let lastRun = 0;

  const norm = (s) => (s || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g,'')
    .toLowerCase().replace(/\s+/g,' ').replace(/[\*:\-–—]+/g,' ').trim();

  function fieldLabel(el) {
    const out = [];
    if (el.id) {
      try {
        document.querySelectorAll('label[for="' + CSS.escape(el.id) + '"]').forEach(x => out.push(x.textContent || ''));
      } catch (_) {}
    }
    const ownLabel = el.closest && el.closest('label');
    if (ownLabel) out.push(ownLabel.textContent || '');
    const td = el.closest && el.closest('td');
    if (td) {
      let p = td.previousElementSibling;
      let n = 0;
      while (p && n < 3) { out.push(p.textContent || ''); p = p.previousElementSibling; n++; }
    }
    const tr = el.closest && el.closest('tr');
    if (tr) out.push((tr.textContent || '').slice(0, 240));
    const parent = el.parentElement;
    if (parent) out.push((parent.textContent || '').slice(0, 180));
    if (el.name) out.push(el.name);
    if (el.id) out.push(el.id);
    return norm(out.join(' | '));
  }

  function candidates() {
    return Array.from(document.querySelectorAll('input:not([type=hidden]), select, textarea'))
      .filter(el => !el.disabled && el.type !== 'button' && el.type !== 'submit');
  }

  function findField(aliases, reject=[]) {
    const aa = aliases.map(norm);
    const rr = reject.map(norm);
    let best = null, bestScore = -1;
    for (const el of candidates()) {
      const label = fieldLabel(el);
      if (!label || rr.some(r => label.includes(r))) continue;
      for (const a of aa) {
        if (!a) continue;
        let score = -1;
        if (label === a) score = 100;
        else if (label.startsWith(a + ' ')) score = 80;
        else if (label.includes(a)) score = 60 - Math.min(30, Math.abs(label.length-a.length)/8);
        if ((norm(el.name).includes(a) || norm(el.id).includes(a)) && score < 75) score = 75;
        if (score > bestScore) { bestScore = score; best = el; }
      }
    }
    return bestScore >= 25 ? best : null;
  }

  function nativeSet(el, value) {
    if (!el || value === undefined || value === null) return false;
    const v = String(value);
    if (el.tagName === 'SELECT') return selectText(el, v);
    if (el.type === 'checkbox' || el.type === 'radio') {
      const want = !!value;
      if (el.checked !== want) el.click();
      return true;
    }
    if (el.value === v) return true;
    try {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) desc.set.call(el, v); else el.value = v;
    } catch (_) { el.value = v; }
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
    el.dispatchEvent(new Event('blur', {bubbles:true}));
    return true;
  }

  function selectText(el, wanted) {
    const w = norm(wanted);
    const opts = Array.from(el.options || []);
    let opt = opts.find(o => norm(o.textContent) === w || norm(o.value) === w);
    if (!opt) opt = opts.find(o => norm(o.textContent).startsWith(w) || w.startsWith(norm(o.textContent)));
    if (!opt) opt = opts.find(o => norm(o.textContent).includes(w) || w.includes(norm(o.textContent)));
    if (!opt && /^\d+$/.test(w)) opt = opts.find(o => norm(o.textContent).startsWith(w + ' ') || norm(o.textContent).startsWith(w + '-'));
    if (!opt) return false;
    if (el.value !== opt.value) {
      el.value = opt.value;
      el.dispatchEvent(new Event('change', {bubbles:true}));
      el.dispatchEvent(new Event('blur', {bubbles:true}));
    }
    return true;
  }

  function setBy(aliases, value, reject=[]) {
    const el = findField(aliases, reject);
    return nativeSet(el, value);
  }

  function clickByText(texts) {
    const ts = texts.map(norm);
    const els = Array.from(document.querySelectorAll('a,button,input[type=button],input[type=submit]'));
    for (const el of els) {
      const txt = norm(el.textContent || el.value || el.title || '');
      if (ts.some(t => txt === t || txt.includes(t))) {
        try { el.click(); return true; } catch (_) {}
      }
    }
    return false;
  }

  function moneyBR(v) {
    const n = Number(v || 0);
    return n.toFixed(2).replace('.', ',');
  }

  function digits(v) { return String(v || '').replace(/\D/g,''); }

  function fillHeader(d) {
    const op = d.operacao || {};
    setBy(['tipo de operacao entrada saida','tipo de operacao'], op.tipo || 'Saída', ['destino']);
    setBy(['tipo de operacao destino','destino interna interestadual exterior'], op.destino || ((d.destinatario||{}).uf === 'RJ' ? 'Interna' : 'Interestadual'));
    setBy(['natureza da operacao'], op.natureza || 'Venda de Mercadoria');
    setBy(['consumidor final'], '1 - Sim');
    setBy(['finalidade de emissao'], '1 - NF-e normal');
    setBy(['tipo atendimento'], '2 - Operação NÃO Presencial, pela INTERNET');
    setBy(['ind intermediador marketplace','intermediador marketplace'], '0 - Operação sem intermediador');
  }

  function fillRecipient(d) {
    const x = d.destinatario || {};
    const doc = digits(x.cpf_cnpj);
    setBy(['cpf cnpj','cnpj cpf','documento'], doc);
    setBy(['nome razao social','razao social','nome destinatario','nome'], x.nome, ['fantasia','emitente']);
    if (x.inscricao_estadual) setBy(['inscricao estadual'], x.inscricao_estadual);
    else setBy(['indicador ie destinatario','indicador da ie','ind ie dest'], '9 - Não Contribuinte');
    setBy(['email','e mail'], x.email);
    setBy(['telefone','fone'], digits(x.telefone));
    setBy(['cep'], digits(x.cep));
    setBy(['logradouro','endereco'], x.logradouro);
    setBy(['numero endereco','numero'], x.numero, ['numero nf','numero pedido','numero fci','numero recopi']);
    setBy(['complemento'], x.complemento);
    setBy(['bairro'], x.bairro);
    setBy(['uf'], x.uf, ['emitente']);
    if (x.municipio_ibge) setBy(['municipio','cidade'], x.municipio_ibge);
    setBy(['municipio','cidade'], x.municipio);
  }

  function fillProduct(d) {
    const p = d.produto || {};
    setBy(['codigo produto','codigo'], p.codigo, ['codigo cest','codigo municipio']);
    setBy(['descricao produto','descricao'], p.descricao);
    setBy(['grupo cfop'], p.grupo_cfop || 'Venda de Mercadoria');
    setBy(['cfop'], p.cfop);
    setBy(['ncm'], p.ncm);
    setBy(['ean tributavel'], p.ean_tributavel || p.ean);
    setBy(['ean'], p.ean, ['tributavel']);
    setBy(['unid comercial','unidade comercial'], p.unidade || 'UN');
    setBy(['qtd comercial','quantidade comercial'], String(p.quantidade || 1).replace('.',','));
    setBy(['valor unit comercial','valor unitario comercial'], moneyBR(p.valor_unitario));
    setBy(['valor produto servico','valor produto','valor total produto'], moneyBR(p.valor_total));
    setBy(['unid tributavel','unidade tributavel'], p.unidade_tributavel || p.unidade || 'UN');
    setBy(['qtd tributavel','quantidade tributavel'], String(p.quantidade || 1).replace('.',','));
    setBy(['valor unit tributavel'], moneyBR(p.valor_unitario));
    const comp = findField(['valor produto compoe total produtos','valor compoe total produtos']);
    if (comp && comp.type === 'checkbox' && !comp.checked) comp.click();
    if (p.numero_serie) setBy(['numero de serie','numero serie'], p.numero_serie);
  }

  function fillTaxes(d) {
    const p = d.produto || {};
    setBy(['origem mercadoria','origem'], p.origem || '0');
    setBy(['csosn'], p.csosn || '400');
    setBy(['cst pis','situacao tributaria pis'], p.pis_cst || '07');
    setBy(['cst cofins','situacao tributaria cofins'], p.cofins_cst || '06');
  }

  function paymentKind(text) {
    const n = norm(text);
    if (n.includes('pix')) return {aliases:['pix'], code:'17', desc:'Pix'};
    if (n.includes('credito')) return {aliases:['cartao de credito','credito'], code:'03', desc:'Cartão de Crédito'};
    if (n.includes('debito')) return {aliases:['cartao de debito','debito'], code:'04', desc:'Cartão de Débito'};
    if (n.includes('dinheiro')) return {aliases:['dinheiro'], code:'01', desc:'Dinheiro'};
    if (n.includes('boleto')) return {aliases:['boleto bancario','boleto'], code:'15', desc:'Boleto'};
    return {aliases:['outros'], code:'99', desc:text || 'Outros'};
  }

  function fillPayment(d) {
    const pays = d.pagamentos || [];
    const p = pays.length ? pays[0] : null;
    if (!p) return;
    const kind = paymentKind(p.forma || p.banco || '');
    const field = findField(['forma de pagamento','meio de pagamento','tipo pagamento','forma pagamento']);
    if (field && field.tagName === 'SELECT') {
      let ok = false;
      for (const a of kind.aliases) if (selectText(field,a)) {ok=true;break;}
      if (!ok) selectText(field, kind.code);
      if (!ok && kind.code === '17') selectText(field, '99');
    }
    setBy(['descricao do pagamento','descricao pagamento','xpag'], kind.desc);
    setBy(['valor do pagamento','valor pagamento','valor pago'], moneyBR(p.valor || (d.totais||{}).recebido || (d.totais||{}).venda));
    setBy(['indicador de pagamento','pagamento'], '0 - Pagamento à Vista');
  }

  function maybeCreateProduct(d) {
    const p = d.produto || {};
    const hasProductFields = !!findField(['ncm']) || !!findField(['grupo cfop']);
    if (hasProductFields) return;
    const pageText = norm(document.body ? document.body.innerText : '');
    if (!pageText.includes('produtos e servicos')) return;
    const codePresent = pageText.includes(norm(p.codigo)) || pageText.includes(norm(p.descricao));
    if (!codePresent) clickByText(['incluir']);
  }

  function runFill(force=false) {
    if (!payload || filling) return;
    const now = Date.now();
    if (!force && now-lastRun < 350) return;
    lastRun = now;
    filling = true;
    try {
      fillHeader(payload);
      fillRecipient(payload);
      maybeCreateProduct(payload);
      fillProduct(payload);
      fillTaxes(payload);
      fillPayment(payload);
      updateBanner('Dados do Organiza carregados. Campos desta tela preenchidos automaticamente.');
    } catch (e) {
      updateBanner('Dados carregados; alguns campos desta tela ainda precisam ser abertos para preencher.');
    } finally {
      filling = false;
    }
  }

  function updateBanner(msg) {
    if (window.top !== window) return;
    let box = document.getElementById('organiza-nfae-box');
    if (!box) {
      box = document.createElement('div');
      box.id = 'organiza-nfae-box';
      box.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:2147483647;background:#fff;border:2px solid #2563eb;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.22);padding:10px 12px;max-width:360px;font:13px Arial;color:#111';
      box.innerHTML = '<div style="font-weight:700;margin-bottom:6px">Organiza → NFA-e</div><div id="organiza-nfae-msg" style="margin-bottom:8px"></div><button id="organiza-nfae-fill" style="margin-right:6px;padding:6px 9px;cursor:pointer">Preencher agora</button><button id="organiza-nfae-clear" style="padding:6px 9px;cursor:pointer">Limpar dados</button>';
      document.documentElement.appendChild(box);
      box.querySelector('#organiza-nfae-fill').addEventListener('click', () => {
        chrome.storage.local.set({organiza_nfae_trigger:Date.now()});
        runFill(true);
      });
      box.querySelector('#organiza-nfae-clear').addEventListener('click', async () => {
        await chrome.storage.local.remove([STORAGE_KEY, SAVED_AT_KEY, 'organiza_nfae_trigger']);
        payload = null;
        box.remove();
      });
    }
    const msgEl = box.querySelector('#organiza-nfae-msg');
    if (msgEl) msgEl.textContent = msg;
  }

  async function loadPayload() {
    const obj = await chrome.storage.local.get([STORAGE_KEY, SAVED_AT_KEY]);
    const saved = obj[SAVED_AT_KEY] || 0;
    if (!obj[STORAGE_KEY] || Date.now()-saved > MAX_AGE_MS) {
      payload = null;
      if (window.top === window) {
        const old = document.getElementById('organiza-nfae-box'); if (old) old.remove();
      }
      return;
    }
    payload = obj[STORAGE_KEY];
    updateBanner('Dados do Organiza encontrados. Abra as abas da NFA-e e os campos serão preenchidos.');
    runFill(true);
    let tries=0;
    const timer=setInterval(()=>{ if (!payload || tries++>15) return clearInterval(timer); runFill(true); }, 900);
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === 'NFAE_PREENCHER') loadPayload();
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes[STORAGE_KEY] || changes.organiza_nfae_trigger) loadPayload();
  });

  const observer = new MutationObserver(() => { if (payload) runFill(false); });
  observer.observe(document.documentElement, {subtree:true, childList:true});
  loadPayload();
})();
