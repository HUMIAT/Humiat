(function () {
  const STORAGE_KEY = 'organiza_nfae_payload';
  const SAVED_AT_KEY = 'organiza_nfae_saved_at';
  const MAX_AGE_MS = 4 * 60 * 60 * 1000;
  let payload = null;
  let filling = false;
  let lastRun = 0;
  let lastActionAt = 0;
  let fillRetryTimer = null;
  let payloadPollTimer = null;
  let payloadFingerprint = '';
  let productCfopRetries = 0;
  let productIncludeOpened = false;
  let destinatarioActivated = false;
  let productWorkflowDone = false;
  let productTabActionAt = 0;
  let paymentNewOpened = false;
  let checkpointAt = 0;
  let workflowActive = false;
  let automationPaused = false;
  let checkpoint = '';

  const SESSION_ACTIVE = 'organiza_nfae_workflow_active';
  const SESSION_PAUSED = 'organiza_nfae_workflow_paused';
  const SESSION_CHECKPOINT = 'organiza_nfae_workflow_checkpoint';
  const SESSION_CHECKPOINT_AT = 'organiza_nfae_workflow_checkpoint_at';

  function loadWorkflowState() {
    try {
      workflowActive = sessionStorage.getItem(SESSION_ACTIVE) === '1';
      automationPaused = sessionStorage.getItem(SESSION_PAUSED) === '1';
      checkpoint = sessionStorage.getItem(SESSION_CHECKPOINT) || '';
      checkpointAt = Number(sessionStorage.getItem(SESSION_CHECKPOINT_AT) || 0);
    } catch (_) {}
  }

  function saveWorkflowState() {
    try {
      sessionStorage.setItem(SESSION_ACTIVE, workflowActive ? '1' : '0');
      sessionStorage.setItem(SESSION_PAUSED, automationPaused ? '1' : '0');
      if (checkpoint) { sessionStorage.setItem(SESSION_CHECKPOINT, checkpoint); sessionStorage.setItem(SESSION_CHECKPOINT_AT, String(checkpointAt || Date.now())); }
      else { sessionStorage.removeItem(SESSION_CHECKPOINT); sessionStorage.removeItem(SESSION_CHECKPOINT_AT); }
    } catch (_) {}
  }

  function isPaused() {
    loadWorkflowState();
    return automationPaused;
  }

  function isWorkflowActive() {
    loadWorkflowState();
    return workflowActive;
  }

  function extensionAlive() {
    try { return !!(chrome && chrome.runtime && chrome.runtime.id); } catch (_) { return false; }
  }

  function isContextInvalidated(err) {
    return String(err && (err.message || err)).includes('Extension context invalidated');
  }

  async function runtimeMessage(message) {
    if (!extensionAlive()) return null;
    try { return await chrome.runtime.sendMessage(message); }
    catch (err) { if (isContextInvalidated(err)) return null; throw err; }
  }

  async function broadcastWorkflow(command, extra = {}) {
    return await runtimeMessage(Object.assign({type:'NFAE_WORKFLOW_BROADCAST', command}, extra));
  }

  const norm = (s) => String(s || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    // A SEFAZ usa rótulos como "Unid. Comercial", "Qtd. Comercial" e
    // "Valor Produto/Serviço". Normaliza toda pontuação para espaço para
    // que o mapeamento não dependa de ponto, barra, hífen ou parênteses.
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ').trim();

  const isVisible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const controlsSelector = 'input:not([type=hidden]):not([type=button]):not([type=submit]), select, textarea';

  function controls(root = document) {
    return Array.from(root.querySelectorAll(controlsSelector)).filter((el) => !el.disabled && isVisible(el));
  }

  function fieldMeta(el) {
    if (!el) return '';
    const bits = [];
    if (el.id) {
      try {
        document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`).forEach((x) => bits.push(x.textContent || ''));
      } catch (_) {}
    }
    const ownLabel = el.closest && el.closest('label');
    if (ownLabel) bits.push(ownLabel.textContent || '');
    const td = el.closest && el.closest('td');
    if (td) {
      bits.push(td.textContent || '');
      let prev = td.previousElementSibling;
      let n = 0;
      while (prev && n++ < 2) {
        bits.push(prev.textContent || '');
        prev = prev.previousElementSibling;
      }
    }
    const tr = el.closest && el.closest('tr');
    if (tr) bits.push(tr.textContent || '');
    if (el.name) bits.push(el.name);
    if (el.id) bits.push(el.id);
    return norm(bits.join(' | '));
  }

  function typeMatches(el, opts = {}) {
    if (!el) return false;
    if (opts.tag && el.tagName !== opts.tag.toUpperCase()) return false;
    if (opts.type && String(el.type || '').toLowerCase() !== opts.type.toLowerCase()) return false;
    if (opts.notTypes && opts.notTypes.includes(String(el.type || '').toLowerCase())) return false;
    return true;
  }

  function textScore(text, wanted) {
    const t = norm(text);
    if (!t) return -1;
    let score = -1;
    for (const a of wanted) {
      if (t === a) score = Math.max(score, 140);
      else if (t.startsWith(a + ' ')) score = Math.max(score, 115);
      else if (t.includes(a)) score = Math.max(score, 85 - Math.min(30, Math.abs(t.length - a.length) / 8));
    }
    return score;
  }

  function findField(aliases, opts = {}) {
    const wanted = aliases.map(norm).filter(Boolean);
    const reject = (opts.reject || []).map(norm).filter(Boolean);
    let best = null;
    let bestScore = -1;

    // 1) Prioriza o texto da própria célula/rótulo e o controle imediatamente ao lado.
    const labelNodes = Array.from(document.querySelectorAll('label,td,th,span,font,b,strong')).filter(isVisible);
    for (const node of labelNodes) {
      const txt = norm(node.textContent || '');
      if (!txt || txt.length > 160 || reject.some((r) => txt.includes(r))) continue;
      const ls = textScore(txt, wanted);
      if (ls < 0) continue;
      const candidates = [];
      if (node.tagName === 'LABEL' && node.htmlFor) {
        const target = document.getElementById(node.htmlFor);
        if (target) candidates.push([target, 35]);
      }
      controls(node).forEach((el, i) => candidates.push([el, 28 - i]));
      const td = node.closest && node.closest('td');
      if (td) {
        controls(td).forEach((el, i) => candidates.push([el, 26 - i]));
        let next = td.nextElementSibling;
        let hop = 0;
        while (next && hop++ < 2) {
          const cs = controls(next);
          cs.forEach((el, i) => candidates.push([el, 24 - hop * 3 - i]));
          if (cs.length) break;
          next = next.nextElementSibling;
        }
      }
      for (const [el, bonus] of candidates) {
        if (!typeMatches(el, opts)) continue;
        const meta = fieldMeta(el);
        if (reject.some((r) => meta.includes(r))) continue;
        const score = ls + bonus;
        if (score > bestScore) { best = el; bestScore = score; }
      }
    }
    if (bestScore >= 80) return best;

    // 2) Fallback por id/name e contexto do controle.
    for (const el of controls()) {
      if (!typeMatches(el, opts)) continue;
      const meta = fieldMeta(el);
      if (!meta || reject.some((r) => meta.includes(r))) continue;
      let score = textScore(meta, wanted);
      const idName = norm(`${el.id || ''} ${el.name || ''}`);
      for (const a of wanted) {
        if (idName === a) score = Math.max(score, 125);
        else if (idName.includes(a)) score = Math.max(score, 92);
      }
      if (score > bestScore) { bestScore = score; best = el; }
    }
    return bestScore >= 25 ? best : null;
  }

  function findSelectByOption(optionAliases) {
    const wanted = optionAliases.map(norm);
    for (const sel of controls().filter((el) => el.tagName === 'SELECT')) {
      const texts = Array.from(sel.options || []).map((o) => norm(o.textContent || o.value || ''));
      if (wanted.some((w) => texts.some((t) => t.includes(w)))) return sel;
    }
    return null;
  }

  function findUfSelect() {
    for (const sel of controls().filter((el) => el.tagName === 'SELECT')) {
      const vals = Array.from(sel.options || []).map((o) => norm(o.value || o.textContent || ''));
      const hasRJ = vals.some((v) => v === 'rj' || v.startsWith('rj '));
      const hasSP = vals.some((v) => v === 'sp' || v.startsWith('sp '));
      if (hasRJ && hasSP) return sel;
    }
    return findField(['uf'], { tag: 'SELECT', reject: ['contribuinte'] });
  }

  function fire(el, type) {
    try { el.dispatchEvent(new Event(type, { bubbles: true })); } catch (_) {}
  }


  function activateLegacyControl(el) {
    if (!el) return false;
    try { el.focus({preventScroll: true}); } catch (_) { try { el.focus(); } catch (_) {} }
    try { el.dispatchEvent(new FocusEvent('focusin', {bubbles: true})); } catch (_) { fire(el, 'focus'); }
    try { el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window})); } catch (_) {}
    try { el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window})); } catch (_) {}
    try { el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window})); } catch (_) { try { el.click(); } catch (_) {} }
    return true;
  }

  function setSelect(el, wanted) {
    if (!el || el.tagName !== 'SELECT') return false;
    const w = norm(wanted);
    if (!w) return false;
    const opts = Array.from(el.options || []);
    let opt = opts.find((o) => norm(o.textContent) === w || norm(o.value) === w);
    if (!opt) opt = opts.find((o) => norm(o.textContent).startsWith(w) || w.startsWith(norm(o.textContent)));
    if (!opt) opt = opts.find((o) => norm(o.textContent).includes(w) || w.includes(norm(o.textContent)));
    if (!opt && /^\d+$/.test(w)) {
      opt = opts.find((o) => norm(o.value) === w || norm(o.textContent).startsWith(w + ' ') || norm(o.textContent).startsWith(w + '-'));
    }
    if (!opt) return false;
    if (el.value !== opt.value) {
      el.value = opt.value;
      fire(el, 'input');
      fire(el, 'change');
      fire(el, 'blur');
    }
    return true;
  }

  function setValue(el, value) {
    if (!el || value === undefined || value === null) return false;
    if (el.tagName === 'SELECT') return setSelect(el, value);
    if (el.type === 'checkbox' || el.type === 'radio') {
      const want = !!value;
      if (el.checked !== want) {
        try { el.click(); } catch (_) { el.checked = want; fire(el, 'change'); }
      }
      return true;
    }
    const v = String(value);
    try {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) desc.set.call(el, v); else el.value = v;
    } catch (_) { el.value = v; }
    fire(el, 'input');
    fire(el, 'keyup');
    fire(el, 'change');
    fire(el, 'blur');
    return String(el.value || '') === v || String(el.value || '').length > 0;
  }

  function setByLabel(aliases, value, opts = {}) {
    const el = findField(aliases, opts);
    return { ok: setValue(el, value), el };
  }

  // Localiza selects estruturais da SEFAZ pelas próprias opções.
  // É mais confiável que depender apenas do HTML antigo dos rótulos.
  function findSelectWithOptions(requiredOptions = []) {
    const wanted = requiredOptions.map(norm).filter(Boolean);
    for (const sel of controls().filter((el) => el.tagName === 'SELECT')) {
      const opts = Array.from(sel.options || []).map((o) => norm(o.textContent || o.value || ''));
      const ok = wanted.every((w) => opts.some((t) => t === w || t.includes(w) || w.includes(t)));
      if (ok) return sel;
    }
    return null;
  }

  function setSelectAndNotify(el, wanted) {
    if (!el || el.tagName !== 'SELECT') return false;
    const before = String(el.value || '');
    const ok = setSelect(el, wanted);
    if (!ok) return false;
    // setSelect já dispara input/change/blur quando o valor realmente muda.
    // Não dispare novamente quando o campo já está correto: na SEFAZ legada,
    // repetir o change provoca novo postback e faz a tela ficar piscando.
    return String(el.value || '') !== '' && (before !== String(el.value || '') || isSelectAt(el, wanted));
  }

  function clickByText(texts, exact = true) {
    const wanted = texts.map(norm);
    const els = Array.from(document.querySelectorAll('a,button,input[type=button],input[type=submit]')).filter(isVisible);
    for (const el of els) {
      const txt = norm(el.textContent || el.value || el.title || '');
      if (wanted.some((w) => exact ? txt === w : (txt === w || txt.includes(w)))) {
        try { el.click(); return true; } catch (_) {}
      }
    }
    return false;
  }

  function clickAction(texts) {
    if (clickByText(texts, true)) return true;
    return clickByText(texts, false);
  }

  function setCheckpoint(name) {
    checkpoint = name || '';
    checkpointAt = checkpoint ? Date.now() : 0;
    saveWorkflowState();
  }

  function checkpointElapsed(ms) {
    loadWorkflowState();
    return !!checkpointAt && (Date.now() - checkpointAt >= ms);
  }

  function findTaxSelect(kind) {
    if (kind === 'pis') {
      return findField(['tributação pis cst', 'tributacao pis cst'], { tag: 'SELECT' }) ||
             findSelectByOption(['PIS 07', 'Oper. Isenta da Contribuição', 'Oper Isenta da Contribuicao']);
    }
    if (kind === 'cofins') {
      return findField(['tributação cofins cst', 'tributacao cofins cst'], { tag: 'SELECT' }) ||
             findSelectByOption(['COFINS 06', 'Alíq. Zero', 'Aliq Zero']);
    }
    return null;
  }

  function setTaxCode(kind, code) {
    const sel = findTaxSelect(kind);
    if (!sel) return false;
    const tries = kind === 'pis'
      ? [code, 'PIS 07', 'PIS 07 - Oper. Isenta da Contribuição']
      : [code, 'COFINS 06', 'COFINS 06 - Oper. Tribut. - Alíq. Zero'];
    for (const value of tries) if (setSelect(sel, value)) return true;
    return false;
  }


  function clickLegacyTab(texts) {
    const wanted = texts.map(norm).filter(Boolean);
    const candidates = Array.from(document.querySelectorAll('a,button,input[type=button],input[type=submit],[onclick],[role=tab]'))
      .filter((el) => isVisible(el) && !(el.closest && el.closest('#organiza-nfae-box')));
    for (const el of candidates) {
      const txt = norm(el.textContent || el.value || el.title || '');
      if (!txt) continue;
      if (wanted.some((w) => txt === w || txt.startsWith(w + ' ') || txt.includes(w))) {
        try {
          el.focus({preventScroll:true});
        } catch (_) { try { el.focus(); } catch (_) {} }
        try { el.click(); return true; } catch (_) {}
      }
    }
    return false;
  }

  function resultOk(results, name) {
    const n = norm(name);
    const item = (results || []).find((x) => norm(x[0]) === n);
    return !!(item && item[1]);
  }

  function pauseAtCheckpoint(name, message, detail = '') {
    workflowActive = true;
    automationPaused = true;
    checkpoint = name || '';
    checkpointAt = Date.now();
    saveWorkflowState();
    updateBanner(message, detail);
    broadcastWorkflow('PAUSE', {checkpoint, message, detail}).catch(() => {});
    return true;
  }

  function advanceProductWorkflow(kind, results) {
    if (!isWorkflowActive() || isPaused()) return false;
    const now = Date.now();

    // Após validar o item, salva automaticamente. Se a SEFAZ impedir o salvamento,
    // para depois de alguns segundos para o usuário ver o erro em tela.
    if (checkpoint === 'produto_validando' && kind.startsWith('produto_')) {
      if (!checkpointElapsed(850)) { scheduleFill(900); return true; }
      if (clickAction(['salvar item'])) {
        setCheckpoint('produto_salvando');
        lastActionAt = now;
        updateBanner('Produto validado.', 'Salvando item automaticamente...');
        scheduleFill(1200);
        return true;
      }
    }
    if (checkpoint === 'produto_salvando' && kind.startsWith('produto_')) {
      if (!checkpointElapsed(4500)) { scheduleFill(1000); return true; }
      pauseAtCheckpoint(
        'produto_erro',
        'A SEFAZ não saiu da tela do produto.',
        'Confira os campos marcados em vermelho. Corrija o que for necessário e clique em Continuar automação.'
      );
      return true;
    }

    if (now - productTabActionAt < 650) return false;
    let next = null;
    if (kind === 'produto_dados') {
      const essentials = ['CFOP', 'Unidade comercial', 'Valor unitário', 'Quantidade', 'Valor produto'];
      if (essentials.every((x) => resultOk(results, x))) next = ['tributos'];
    } else if (kind === 'produto_icms') {
      if (resultOk(results, 'Origem') && resultOk(results, 'ICMS/CSOSN')) next = ['pis'];
    } else if (kind === 'produto_pis') {
      if (resultOk(results, 'PIS CST')) next = ['cofins'];
    } else if (kind === 'produto_cofins') {
      if (resultOk(results, 'COFINS CST')) next = ['inf adicionais', 'informações adicionais', 'informacoes adicionais'];
    } else if (kind === 'produto_adicional') {
      if (resultOk(results, 'Informações adicionais')) {
        productWorkflowDone = true;
        if (clickAction(['validar item'])) {
          setCheckpoint('produto_validando');
          lastActionAt = now;
          updateBanner('Produto preenchido.', 'Validando item automaticamente...');
          scheduleFill(1000);
          return true;
        }
        pauseAtCheckpoint(
          'produto_validar_manual',
          'Produto preenchido.',
          'Não localizei o botão Validar Item. Valide/salve manualmente e clique em Continuar automação.'
        );
        return true;
      }
      return false;
    }

    if (next && clickLegacyTab(next)) {
      productTabActionAt = now;
      scheduleFill(850);
      setTimeout(() => scheduleFill(250), 1200);
      return true;
    }
    return false;
  }

  function productAlreadyListed() {
    const p = (payload && payload.produto) || {};
    const txt = bodyText();
    const code = norm(p.codigo || '');
    const desc = norm(p.descricao || '');
    return !!((code && txt.includes(code)) || (desc && txt.includes(desc)));
  }

  function paymentLooksSaved() {
    const txt = bodyText();
    if (txt.includes('nao ha item') || txt.includes('nenhum item')) return false;
    const p = pagamentoInfo(payload || {});
    const val = norm(moneyBR(p.valor));
    const desc = norm(p.descricao || '');
    return (val && txt.includes(val)) || (desc && txt.includes(desc) && txt.includes('pagamento'));
  }

  function advanceMainWorkflow(kind, results) {
    if (!isWorkflowActive() || isPaused()) return false;
    const now = Date.now();
    if (now - lastActionAt < 650) return false;

    if (kind === 'nfe') {
      const essentials = ['Natureza da operação', 'Consumidor final', 'Finalidade', 'Tipo atendimento', 'Intermediador'];
      if (essentials.every((x) => resultOk(results, x))) {
        if (clickLegacyTab(['destinatário/remetente', 'destinatario/remetente', 'destinatário', 'destinatario'])) {
          lastActionAt = now;
          updateBanner('Natureza confirmada.', 'Abrindo Destinatário/Remetente automaticamente...');
          scheduleFill(900);
          return true;
        }
      }
    }

    if (kind === 'destinatario') {
      const essentials = ['Tipo documento', 'Nome/Razão Social', 'Logradouro', 'Número', 'Bairro', 'CEP', 'UF', 'Município'];
      if (essentials.every((x) => resultOk(results, x))) {
        if (clickLegacyTab(['produtos e serviços', 'produtos e servicos'])) {
          lastActionAt = now;
          updateBanner('Destinatário preenchido.', 'Abrindo Produtos e Serviços...');
          scheduleFill(850);
          return true;
        }
      }
    }

    if (kind === 'produtos_lista') {
      if (checkpoint === 'produto_salvando' || productAlreadyListed()) {
        setCheckpoint('');
        if (clickLegacyTab(['transporte'])) {
          lastActionAt = now;
          updateBanner('Produto salvo.', 'Seguindo para Transporte...');
          scheduleFill(850);
          return true;
        }
      }
    }

    if (kind === 'transporte' && resultOk(results, 'Modalidade frete')) {
      setCheckpoint('pagamento_abrir');
      paymentNewOpened = false;
      if (clickLegacyTab(['pagamento'])) {
        lastActionAt = now;
        updateBanner('Transporte preenchido.', 'Abrindo Pagamento...');
        scheduleFill(850);
        return true;
      }
    }

    if (kind === 'pagamento_detalhe') {
      const essentials = ['Meio de pagamento', 'Valor do pagamento', 'Forma de pagamento'];
      if (essentials.every((x) => resultOk(results, x))) {
        if (checkpoint !== 'pagamento_salvando') {
          if (clickAction(['salvar', 'salvar item'])) {
            setCheckpoint('pagamento_salvando');
            lastActionAt = now;
            updateBanner('Pagamento preenchido.', 'Salvando pagamento automaticamente...');
            scheduleFill(1200);
            return true;
          }
          pauseAtCheckpoint(
            'pagamento_salvar_manual',
            'Pagamento preenchido.',
            'Não localizei o botão Salvar. Salve manualmente e clique em Continuar automação.'
          );
          return true;
        }
        if (checkpoint === 'pagamento_salvando' && checkpointElapsed(4500)) {
          pauseAtCheckpoint(
            'pagamento_erro',
            'A SEFAZ não saiu da tela do pagamento.',
            'Confira os campos obrigatórios. Corrija o que for necessário e clique em Continuar automação.'
          );
          return true;
        }
      }
    }

    if (kind === 'pagamento_lista' && (checkpoint === 'pagamento_salvando' || paymentLooksSaved())) {
      setCheckpoint('');
      if (clickLegacyTab(['observação', 'observacao'])) {
        lastActionAt = now;
        updateBanner('Pagamento salvo.', 'Abrindo Observação...');
        scheduleFill(850);
        return true;
      }
    }

    if (kind === 'observacao' && resultOk(results, 'Informações de interesse do Fisco')) {
      pauseAtCheckpoint(
        'revisao_final',
        'Preenchimento automático concluído.',
        'Revise a nota. A emissão final continua sob seu controle: clique em Validar/Salvar/Emitir somente quando estiver tudo correto.'
      );
      return true;
    }
    return false;
  }

  function digits(v) { return String(v || '').replace(/\D/g, ''); }
  function moneyBR(v) {
    const n = Number(v || 0);
    return Number.isFinite(n) ? n.toFixed(2).replace('.', ',') : '0,00';
  }
  function qtyBR(v) {
    const n = Number(v || 1);
    return Number.isFinite(n) ? n.toFixed(4).replace('.', ',') : '1,0000';
  }

  function bodyText() { return norm(document.body ? document.body.innerText : ''); }
  function hasVisibleLabel(aliases, opts = {}) { return !!findField(aliases, opts); }

  function pageKind() {
    const txt = bodyText();
    const hash = norm(location.hash || '');

    // Nunca tocar no emitente: é a própria empresa emissora já carregada pela SEFAZ.
    if (txt.includes('tipo inscricao rfb') || txt.includes('nome fantasia') || txt.includes('regime tributario')) return 'emitente';

    // Janelas internas de Produto/Serviço.
    if (hasVisibleLabel(['tributação icms cst csosn', 'tributacao icms cst csosn'], { tag: 'SELECT' })) return 'produto_icms';
    if (hasVisibleLabel(['tributação pis cst', 'tributacao pis cst'], { tag: 'SELECT' })) return 'produto_pis';
    if (hasVisibleLabel(['tributação cofins cst', 'tributacao cofins cst'], { tag: 'SELECT' })) return 'produto_cofins';
    if (hasVisibleLabel(['informações adicionais', 'informacoes adicionais'], { tag: 'TEXTAREA' }) && txt.includes('produtos e servicos')) return 'produto_adicional';
    if (hasVisibleLabel(['ncm'], { notTypes: ['checkbox', 'radio'] }) && hasVisibleLabel(['grupo cfop'], { tag: 'SELECT' })) return 'produto_dados';

    // Janela interna de pagamento.
    if (hasVisibleLabel(['meio de pagamento'], { tag: 'SELECT' }) && hasVisibleLabel(['valor do pagamento'], { notTypes: ['checkbox', 'radio'] })) return 'pagamento_detalhe';

    // Lista principal de Produtos e Serviços. O portal antigo nem sempre
    // expõe a palavra "linhas"; usa também os cabeçalhos reais da tabela.
    if (txt.includes('produtos e servicos') && txt.includes('ncm') && txt.includes('cfop') &&
        txt.includes('cst icms') && (txt.includes('v unit') || txt.includes('valor unit'))) return 'produtos_lista';

    // Abas principais.
    if (txt.includes('tipo de documento') && txt.includes('razao social nome')) return 'destinatario';
    // A tela principal da NFA-e pode estar dentro de um frame e o texto dos
    // rótulos nem sempre é exposto de forma estável. Detecta também pelos
    // selects estruturais da própria SEFAZ.
    const selTipoOp = findSelectWithOptions(['Entrada', 'Saída']);
    const selDestino = findSelectWithOptions(['Interna', 'Interestadual']);
    if ((selTipoOp && selDestino) || (txt.includes('tipo de operacao') && txt.includes('natureza da operacao'))) return 'nfe';
    if (txt.includes('informacoes adicionais de interesse do fisco')) return 'observacao';
    if (hash.includes('produtos') || (txt.includes('produtos e servicos') && txt.includes('linhas'))) return 'produtos_lista';
    const hasNovoPagamento = Array.from(document.querySelectorAll('a,button,input[type=button],input[type=submit]')).filter(isVisible).some((el) => ['novo','incluir'].includes(norm(el.textContent || el.value || el.title || '')));
    if (hash.includes('pagamento') || (txt.includes('pagamentos') && txt.includes('linhas')) || (txt.includes('pagamento') && hasNovoPagamento)) return 'pagamento_lista';
    if (txt.includes('total nota fiscal') || txt.includes('base de calculo icms')) return 'total';
    if (txt.includes('transporte')) return 'transporte';
    if (txt.includes('referencias')) return 'referencias';
    if (txt.includes('cobranca')) return 'cobranca';
    return 'outro';
  }

  function selectedText(sel) {
    if (!sel || sel.tagName !== 'SELECT') return '';
    const opt = sel.options && sel.options[sel.selectedIndex];
    return norm(opt ? (opt.textContent || opt.value) : sel.value);
  }

  function isSelectAt(sel, wanted) {
    const cur = selectedText(sel);
    const w = norm(wanted);
    return !!cur && (cur === w || cur.includes(w) || w.includes(cur));
  }

  function findNaturezaSelect() {
    return findField(['natureza da operação', 'natureza da operacao'], { tag: 'SELECT' }) ||
           findSelectByOption(['Venda de Mercadoria']);
  }

  function fillNfe(d) {
    const results = [];

    // A primeira etapa da NF-e fica manual. A extensão NÃO altera:
    // - Tipo de operação
    // - Tipo de operação (Destino)
    // - Natureza da operação
    // Ela apenas aguarda a Natureza já estar em "Venda de Mercadoria".
    // Assim evitamos os postbacks/loops da tela antiga da SEFAZ.
    const natureza = findNaturezaSelect();
    const naturezaOk = !!(natureza && isSelectAt(natureza, 'Venda de Mercadoria'));
    results.push(['Natureza da operação', naturezaOk]);

    if (!naturezaOk) {
      updateBanner(
        'Aguardando Natureza da operação.',
        'Preencha manualmente "Venda de Mercadoria". Assim que estiver selecionado, a automação continua sozinha.'
      );
      return results;
    }

    // Natureza confirmada: daqui em diante a automação assume o fluxo.
    workflowActive = true;
    automationPaused = false;
    checkpoint = '';
    saveWorkflowState();

    results.push(['Consumidor final', setByLabel(['consumidor final'], '1 - Sim', { tag: 'SELECT' }).ok]);
    results.push(['Finalidade', setByLabel(['finalidade de emissão', 'finalidade de emissao'], '1 - NF-e normal', { tag: 'SELECT' }).ok]);
    results.push(['Tipo atendimento', setByLabel(['tipo atendimento'], '2 - Operação NÃO Presencial, pela INTERNET', { tag: 'SELECT' }).ok]);
    results.push(['Intermediador', setByLabel(['ind intermediador marketplace', 'intermediador marketplace'], '0 - Operação sem intermediador', { tag: 'SELECT' }).ok]);
    return results;
  }

  function findIeInput() {
    return findField(['inscrição estadual', 'inscricao estadual'], { notTypes: ['checkbox', 'radio'], reject: ['st'] });
  }

  function findSemIeCheckbox() {
    return findField(['sem inscrição estadual', 'sem inscricao estadual'], { type: 'checkbox' });
  }

  function fillDestinatario(d) {
    const x = d.destinatario || {};
    const doc = digits(x.cpf_cnpj);
    const tipo = x.tipo_documento || (doc.length === 11 ? 'CPF' : doc.length === 14 ? 'CNPJ' : '');
    const results = [];

    const tipoSelect = findField(['tipo de documento'], { tag: 'SELECT' });

    // A tela legada do Destinatário/Remetente só inicializa corretamente
    // depois que o primeiro campo recebe foco/clique. O usuário confirmou que,
    // ao clicar manualmente em "Tipo de documento", o restante passa a ser
    // preenchido. Reproduz esse gesto uma única vez e só depois continua.
    if (tipoSelect && !destinatarioActivated) {
      destinatarioActivated = true;
      activateLegacyControl(tipoSelect);
      scheduleFill(280);
      return [['Ativação da tela do destinatário', true]];
    }
    const beforeTipo = tipoSelect ? norm(tipoSelect.options[tipoSelect.selectedIndex]?.textContent || tipoSelect.value) : '';
    const tipoOk = tipoSelect ? setSelect(tipoSelect, tipo) : false;
    results.push(['Tipo documento', tipoOk]);
    // Alguns formulários da SEFAZ reconstroem CPF/CNPJ ao trocar o tipo. A próxima rodada termina o preenchimento.
    if (tipoSelect && tipoOk && beforeTipo && !beforeTipo.includes(norm(tipo))) return results;

    const docLabels = tipo === 'CPF' ? ['cpf'] : ['cnpj'];
    results.push([tipo || 'Documento', setByLabel(docLabels, doc, { notTypes: ['checkbox', 'radio'] }).ok]);
    const nomeFiscal = tipo === 'CNPJ' ? (x.razao_social || '') : (x.nome || '');
    results.push(['Nome/Razão Social', setByLabel(['razão social nome', 'razao social nome'], nomeFiscal, { notTypes: ['checkbox', 'radio'] }).ok]);

    const ie = String(x.inscricao_estadual || '').trim();
    const semIe = x.sem_inscricao_estadual === true || !ie || ie === '-';
    const semIeBox = findSemIeCheckbox();
    if (semIeBox) setValue(semIeBox, semIe);
    results.push(['Sem IE', !!semIeBox]);
    if (!semIe) {
      const ieOk = setValue(findIeInput(), ie);
      results.push(['Inscrição Estadual', ieOk]);
      const contrib = findSelectByOption(['contribuinte icms', 'contribuinte']);
      if (contrib) setSelect(contrib, 'CONTRIBUINTE');
    } else {
      const contrib = findSelectByOption(['não contribuinte', 'nao contribuinte']);
      results.push(['Não contribuinte', contrib ? setSelect(contrib, 'NÃO CONTRIBUINTE') : true]);
    }

    if (x.email) results.push(['E-mail', setByLabel(['e mail', 'email'], x.email, { notTypes: ['checkbox', 'radio'] }).ok]);
    results.push(['Logradouro', setByLabel(['logradouro'], x.logradouro, { notTypes: ['checkbox', 'radio'] }).ok]);
    results.push(['Número', setByLabel(['nro', 'número', 'numero'], x.numero, { notTypes: ['checkbox', 'radio'], reject: ['nf e', 'pedido', 'fci', 'recopi'] }).ok]);
    if (x.complemento) results.push(['Complemento', setByLabel(['complemento'], x.complemento, { notTypes: ['checkbox', 'radio'] }).ok]);
    results.push(['Bairro', setByLabel(['bairro distrito', 'bairro'], x.bairro, { notTypes: ['checkbox', 'radio'] }).ok]);
    results.push(['CEP', setByLabel(['cep'], digits(x.cep), { notTypes: ['checkbox', 'radio'] }).ok]);
    if (x.telefone) results.push(['Fone', setByLabel(['fone'], digits(x.telefone), { notTypes: ['checkbox', 'radio'] }).ok]);

    const uf = String(x.uf || '').trim().toUpperCase();
    const ufSelect = findUfSelect();
    const oldUF = ufSelect ? norm(ufSelect.options[ufSelect.selectedIndex]?.textContent || ufSelect.value) : '';
    const ufOk = ufSelect ? setSelect(ufSelect, uf) : false;
    results.push(['UF', ufOk]);
    // O município depende da UF; se a UF acabou de mudar, a próxima rodada aguarda a lista da SEFAZ.
    if (ufSelect && ufOk && oldUF && !oldUF.includes(norm(uf))) return results;

    const mun = findField(['município', 'municipio'], { tag: 'SELECT' });
    let munOk = false;
    if (mun) munOk = setSelect(mun, x.municipio || x.municipio_ibge || '');
    results.push(['Município', munOk]);
    // País permanece BRASIL e os checkboxes de retirada/entrega permanecem intocados.
    return results;
  }

  function fillProdutoDados(d) {
    const p = d.produto || {};
    const results = [];
    results.push(['Código', setByLabel(['código', 'codigo'], p.codigo, { notTypes: ['checkbox', 'radio'], reject: ['cest'] }).ok]);
    results.push(['Descrição', setByLabel(['descrição', 'descricao'], p.descricao, { notTypes: ['checkbox', 'radio'] }).ok]);

    // Grupo CFOP precisa ser definido antes do CFOP. A SEFAZ libera/recarrega
    // as opções do CFOP somente depois que o grupo é processado.
    const grupo = findField(['grupo cfop'], { tag: 'SELECT' });
    const grupoOk = grupo ? setSelect(grupo, p.grupo_cfop || 'Venda de Mercadoria') : false;
    results.push(['Grupo CFOP', grupoOk]);

    let cfop = findField(['cfop'], { tag: 'SELECT', reject: ['grupo'] });
    if (!cfop) cfop = findSelectByOption([p.cfop, '5102', '6102']);

    // Como os três campos iniciais da NF-e ficaram manuais, a própria SEFAZ
    // passa a ser a fonte de verdade para o CFOP disponível nesta nota. Ex.:
    // se a tela foi configurada como Interna, ela oferece 5102; se for
    // Interestadual, normalmente oferece 6102. Primeiro tenta o CFOP vindo do
    // Organiza e, se ele não existir nas opções atuais, escolhe somente o CFOP
    // de venda de mercadoria (5102/6102) que a SEFAZ realmente disponibilizou.
    let cfopOk = false;
    if (cfop) {
      try { cfop.focus({preventScroll: true}); } catch (_) { try { cfop.focus(); } catch (_) {} }
      const available = Array.from(cfop.options || []);
      const hasCode = (code) => available.some((o) => {
        const t = norm(`${o.value || ''} ${o.textContent || ''}`);
        return t === norm(code) || t.startsWith(norm(code) + ' ') || t.includes(norm(code) + ' venda');
      });
      const desired = String(p.cfop || '').trim();
      const candidates = [];
      if (desired) candidates.push(desired);
      if (!candidates.includes('5102')) candidates.push('5102');
      if (!candidates.includes('6102')) candidates.push('6102');
      const usable = candidates.find(hasCode);
      if (usable) cfopOk = setSelect(cfop, usable);
    }
    results.push(['CFOP', cfopOk]);
    if (grupoOk && !cfopOk && productCfopRetries < 8) {
      productCfopRetries += 1;
      scheduleFill(850);
    } else if (cfopOk) {
      productCfopRetries = 0;
    }

    results.push(['NCM', setByLabel(['ncm'], p.ncm || '95045000', { notTypes: ['checkbox', 'radio'] }).ok]);
    results.push(['EAN', setByLabel(['ean'], p.ean || 'SEM GTIN', { notTypes: ['checkbox', 'radio'], reject: ['tributavel', 'tributável'] }).ok]);
    results.push(['Unidade comercial', setByLabel(['unid comercial', 'unidade comercial'], p.unidade || 'UN', { tag: 'SELECT' }).ok]);
    results.push(['Valor unitário', setByLabel(['valor unit comercial', 'valor unitário comercial', 'valor unitario comercial'], moneyBR(p.valor_unitario), { notTypes: ['checkbox', 'radio'] }).ok]);
    results.push(['Quantidade', setByLabel(['qtd comercial', 'quantidade comercial'], qtyBR(p.quantidade || 1), { notTypes: ['checkbox', 'radio'] }).ok]);
    results.push(['EAN tributável', setByLabel(['ean tributável', 'ean tributavel'], p.ean_tributavel || p.ean || 'SEM GTIN', { notTypes: ['checkbox', 'radio'] }).ok]);
    results.push(['Valor produto', setByLabel(['valor produto serviço', 'valor produto servico'], moneyBR(p.valor_total), { notTypes: ['checkbox', 'radio'] }).ok]);
    const comp = findField(['valor produto compõe total produtos', 'valor produto compoe total produtos'], { type: 'checkbox' });
    if (comp) setValue(comp, true);
    results.push(['Compõe total', !!comp]);
    // Frete, desconto, seguro, CEST, pedidos e demais campos permanecem vazios.
    return results;
  }

  function findRadioNear(alias) {
    const a = norm(alias);
    for (const r of controls().filter((el) => el.type === 'radio')) {
      if (fieldMeta(r).includes(a)) return r;
      const parent = r.parentElement;
      if (parent && norm(parent.textContent).includes(a)) return r;
    }
    return null;
  }

  function fillProdutoIcms(d) {
    const p = d.produto || {};
    const results = [];
    const icmsRadio = findRadioNear('icms');
    if (icmsRadio) setValue(icmsRadio, true);
    results.push(['Informar ICMS', !!icmsRadio]);
    results.push(['Origem', setByLabel(['origem'], p.origem || '0 - Nacional', { tag: 'SELECT' }).ok]);
    results.push(['ICMS/CSOSN', setByLabel(['tributação icms cst csosn', 'tributacao icms cst csosn'], p.csosn || '400', { tag: 'SELECT' }).ok]);
    // Regime vem da SEFAZ (Simples Nacional - MEI) e valores permanecem vazios/zero.
    return results;
  }

  function fillProdutoPis(d) {
    const p = d.produto || {};
    return [['PIS CST', setTaxCode('pis', p.pis_cst || '07')]];
  }

  function fillProdutoCofins(d) {
    const p = d.produto || {};
    return [['COFINS CST', setTaxCode('cofins', p.cofins_cst || '06')]];
  }

  function fillProdutoAdicional(d) {
    const p = d.produto || {};
    const texto = p.informacao_adicional || d.observacao_fiscal || '';
    return [['Informações adicionais', setByLabel(['informações adicionais', 'informacoes adicionais'], texto, { tag: 'TEXTAREA' }).ok]];
  }

  function pagamentoInfo(d) {
    const explicit = d.pagamento_nfae || {};
    const first = (d.pagamentos || [])[0] || {};
    const forma = String(explicit.forma || first.forma || first.banco || '').trim();
    const n = norm(forma);
    let descricao = forma || 'Outros';
    let meio = ['99 - Outros', '99', 'Outros'];
    if (n.includes('pix')) { descricao = 'Pix'; meio = ['99 - Outros', '99', 'Outros']; }
    else if (n.includes('credito')) { descricao = 'Cartão de Crédito'; meio = ['03', 'Cartão de Crédito']; }
    else if (n.includes('debito')) { descricao = 'Cartão de Débito'; meio = ['04', 'Cartão de Débito']; }
    else if (n.includes('dinheiro')) { descricao = 'Dinheiro'; meio = ['01', 'Dinheiro']; }
    else if (n.includes('boleto')) { descricao = 'Boleto'; meio = ['15', 'Boleto']; }
    const valor = Number(explicit.valor || first.valor || (d.totais || {}).recebido || (d.totais || {}).venda || 0);
    return { descricao, meio, valor };
  }

  function fillTransporte(d) {
    const results = [];
    // Padrão definido pelo usuário para as vendas de equipamentos: 9 - Sem frete.
    // Não preenche transportador, endereço, veículo ou volumes.
    let modalidade = findField(['modalidade frete', 'modalidade de frete'], { tag: 'SELECT' });
    if (!modalidade) modalidade = findSelectByOption(['9 - Sem frete', 'Sem frete']);
    if (modalidade) activateLegacyControl(modalidade);
    results.push(['Modalidade frete', modalidade ? setSelect(modalidade, '9 - Sem frete') : false]);
    return results;
  }

  function fillPagamento(d) {
    const p = pagamentoInfo(d);
    const results = [];
    const meio = findField(['meio de pagamento'], { tag: 'SELECT' });
    let meioOk = false;
    if (meio) {
      for (const v of p.meio) { if (setSelect(meio, v)) { meioOk = true; break; } }
    }
    results.push(['Meio de pagamento', meioOk]);
    results.push(['Valor do pagamento', setByLabel(['valor do pagamento'], moneyBR(p.valor), { notTypes: ['checkbox', 'radio'] }).ok]);
    results.push(['Forma de pagamento', setByLabel(['forma de pagamento'], '0 - Pagamento à Vista', { tag: 'SELECT' }).ok]);
    results.push(['Descrição do meio', setByLabel(['descrição do meio de pagamento', 'descricao do meio de pagamento'], p.descricao, { notTypes: ['checkbox', 'radio'] }).ok]);
    return results;
  }

  function fillObservacao(d) {
    const texto = d.observacao_fiscal || (d.produto || {}).informacao_adicional || '';
    return [['Informações de interesse do Fisco', setByLabel(['informações adicionais de interesse do fisco', 'informacoes adicionais de interesse do fisco'], texto, { tag: 'TEXTAREA' }).ok]];
  }

  function maybeOpenDetail(kind) {
    const now = Date.now();
    if (now - lastActionAt < 1600) return false;
    const txt = bodyText();
    if (kind === 'produtos_lista') {
      const p = (payload && payload.produto) || {};
      const code = norm(p.codigo || '');
      const desc = norm(p.descricao || '');
      const alreadyListed = (code && txt.includes(code)) || (desc && txt.includes(desc));
      const looksEmpty = txt.includes('nao ha item') || txt.includes('nenhum item') || txt.includes('nenhum produto');
      if (!alreadyListed && !productIncludeOpened && (looksEmpty || txt.includes('produtos e servicos'))) {
        if (clickByText(['incluir'], true)) {
          productIncludeOpened = true;
          lastActionAt = now;
          return true;
        }
      }
    }
    if (kind === 'pagamento_lista') {
      const precisaAbrir = checkpoint === 'pagamento_abrir' || !paymentLooksSaved();
      if (precisaAbrir && !paymentNewOpened) {
        if (clickAction(['novo', 'incluir'])) {
          paymentNewOpened = true;
          setCheckpoint('pagamento_preencher');
          lastActionAt = now;
          scheduleFill(900);
          return true;
        }
      }
    }
    return false;
  }

  function summarize(kind, results) {
    if (!results || !results.length) return null;
    const ok = results.filter((x) => x[1]).length;
    const missing = results.filter((x) => !x[1]).map((x) => x[0]);
    return { kind, ok, total: results.length, missing };
  }

  function updateBanner(msg, detail = '') {
    if (window.top !== window) return;
    let box = document.getElementById('organiza-nfae-box');
    if (!box) {
      box = document.createElement('div');
      box.id = 'organiza-nfae-box';
      box.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:2147483647;background:#fff;border:2px solid #2563eb;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.22);padding:10px 12px;max-width:430px;font:13px Arial;color:#111';
      box.innerHTML = '<div style="font-weight:700;margin-bottom:6px">Organiza → NFA-e</div><div id="organiza-nfae-msg" style="margin-bottom:4px"></div><div id="organiza-nfae-detail" style="font-size:11px;color:#555;margin-bottom:8px"></div><button id="organiza-nfae-fill" style="margin-right:6px;padding:6px 9px;cursor:pointer">Iniciar automático</button><button id="organiza-nfae-pause" style="margin-right:6px;padding:6px 9px;cursor:pointer">Pausar</button><button id="organiza-nfae-clear" style="padding:6px 9px;cursor:pointer">Limpar dados</button>';
      document.documentElement.appendChild(box);
      box.querySelector('#organiza-nfae-fill').addEventListener('click', async () => {
        workflowActive = true;
        automationPaused = false;
        checkpoint = '';
        saveWorkflowState();
        updateBanner('Automação iniciada.', 'Ela seguirá pelas telas até um ponto de revisão. Você pode pausar a qualquer momento.');
        await broadcastWorkflow('START');
        runFill(true);
      });
      box.querySelector('#organiza-nfae-pause').addEventListener('click', async () => {
        loadWorkflowState();
        automationPaused = !automationPaused;
        if (!automationPaused) workflowActive = true;
        saveWorkflowState();
        await broadcastWorkflow(automationPaused ? 'PAUSE' : 'RESUME', {checkpoint});
        updateBanner(automationPaused ? 'Automação pausada.' : 'Automação retomada.', automationPaused ? 'Você está livre para alterar qualquer campo manualmente.' : 'Continuando a partir da tela atual.');
        if (!automationPaused) runFill(true);
      });
      box.querySelector('#organiza-nfae-clear').addEventListener('click', async () => {
        const answer = await runtimeMessage({type:'NFAE_CLEAR_PAYLOAD'});
        payload = null;
        if (answer && answer.ok) box.remove();
        else updateBanner('Não foi possível limpar os dados.', 'Atualize esta aba da SEFAZ e tente novamente.');
      });
    }
    const m = box.querySelector('#organiza-nfae-msg');
    if (m && m.textContent !== String(msg || '')) m.textContent = String(msg || '');
    const det = box.querySelector('#organiza-nfae-detail');
    const detailText = String(detail || '');
    if (det && det.textContent !== detailText) det.textContent = detailText;
    const pauseBtn = box.querySelector('#organiza-nfae-pause');
    if (pauseBtn) {
      loadWorkflowState();
      pauseBtn.textContent = automationPaused ? 'Continuar automação' : 'Pausar';
      pauseBtn.style.fontWeight = automationPaused ? '700' : '400';
    }
  }

  function noTouchMessage(kind) {
    const messages = {
      emitente: 'Emitente é a Karaokê RJ e não será alterado pelo Organiza.',
      total: 'Aba Total: cálculo deixado para a SEFAZ.',
      referencias: 'Aba Referências: permanece vazia.',
      cobranca: 'Aba Cobrança: permanece vazia.'
    };
    return messages[kind] || 'Dados do Organiza carregados. Abra uma aba da NFA-e para preencher.';
  }

  function runFill(force = false) {
    loadWorkflowState();
    if (automationPaused) {
      updateBanner('Automação pausada.', checkpoint ? 'Ponto de revisão: ' + checkpoint + '. Faça as alterações que quiser e clique em Continuar automação.' : 'Faça as alterações que quiser e clique em Continuar automação.');
      return;
    }
    if (!extensionAlive()) {
      updateBanner('Extensão foi atualizada/recarregada.', 'Pressione Ctrl+F5 nesta aba da SEFAZ. Depois volte ao Organiza e clique em Preencher NFA-e automaticamente.');
      return;
    }
    if (!payload || filling) return;
    const now = Date.now();
    if (!force && now - lastRun < 350) return;
    lastRun = now;
    filling = true;
    try {
      const kind = pageKind();
      if (maybeOpenDetail(kind)) {
        updateBanner('Abrindo formulário para preencher...');
        return;
      }
      let res = [];
      if (kind === 'nfe') res = fillNfe(payload);
      else if (kind === 'destinatario') res = fillDestinatario(payload);
      else if (kind === 'produto_dados') res = fillProdutoDados(payload);
      else if (kind === 'produto_icms') res = fillProdutoIcms(payload);
      else if (kind === 'produto_pis') res = fillProdutoPis(payload);
      else if (kind === 'produto_cofins') res = fillProdutoCofins(payload);
      else if (kind === 'produto_adicional') res = fillProdutoAdicional(payload);
      else if (kind === 'pagamento_detalhe') res = fillPagamento(payload);
      else if (kind === 'observacao') res = fillObservacao(payload);
      else if (kind === 'transporte') res = fillTransporte(payload);
      else if (['emitente', 'total', 'referencias', 'cobranca'].includes(kind)) {
        updateBanner(noTouchMessage(kind), 'Nenhum campo desta tela será alterado.');
        return;
      } else if (kind === 'produtos_lista') {
        updateBanner('Produtos e Serviços', bodyText().includes('nao ha item para ser listado') ? 'Abrindo Incluir automaticamente...' : 'Produto já listado; seguindo o fluxo.');
        advanceMainWorkflow(kind, []);
        return;
      } else if (kind === 'pagamento_lista') {
        updateBanner('Pagamento', bodyText().includes('nao ha item para ser listado') ? 'Abrindo Incluir automaticamente...' : 'Pagamento já listado; seguindo o fluxo.');
        advanceMainWorkflow(kind, []);
        return;
      } else {
        updateBanner(noTouchMessage(kind));
        return;
      }

      const sum = summarize(kind, res);
      if (sum) {
        const detail = sum.missing.length ? 'Não localizados nesta tela: ' + sum.missing.join(', ') : 'Todos os campos previstos desta tela foram localizados.';
        updateBanner(`Preenchimento: ${sum.ok}/${sum.total} campos`, detail);
        if (!advanceProductWorkflow(kind, res)) advanceMainWorkflow(kind, res);
      }
    } catch (e) {
      updateBanner('Dados carregados, mas houve falha em alguns campos.', String(e && e.message || e));
    } finally {
      filling = false;
    }
  }

  function fingerprint(data) {
    try { return JSON.stringify(data || {}); } catch (_) { return String(Date.now()); }
  }

  function scheduleFill(delay = 700) {
    if (fillRetryTimer) clearTimeout(fillRetryTimer);
    fillRetryTimer = setTimeout(() => {
      fillRetryTimer = null;
      if (payload && !isPaused()) runFill(true);
    }, delay);
  }

  function acceptPayload(data) {
    if (!data) return;
    const fp = fingerprint(data);
    const changed = fp !== payloadFingerprint;
    payload = data;
    if (!changed) return;
    payloadFingerprint = fp;
    productCfopRetries = 0;
    productIncludeOpened = false;
    destinatarioActivated = false;
    productWorkflowDone = false;
    productTabActionAt = 0;
    paymentNewOpened = false;
    checkpointAt = 0;

    // Um novo pedido vindo do Organiza já inicia o fluxo. O botão Pausar
    // continua disponível e, se o usuário pausar, consultas repetidas do mesmo
    // payload não reativam a automação.
    workflowActive = true;
    automationPaused = false;
    checkpoint = '';
    saveWorkflowState();
    updateBanner('Dados do Organiza carregados.', 'Aguardando Natureza da operação = Venda de Mercadoria. Depois disso o fluxo continua automaticamente.');
    runFill(true);
    scheduleFill(1200);
  }

  async function requestPayloadOnce() {
    const answer = await runtimeMessage({type:'NFAE_GET_PAYLOAD'});
    if (answer && answer.payload) acceptPayload(answer.payload);
  }

  // A página da SEFAZ não usa chrome.storage. Assim, recarregar/atualizar a extensão
  // não deixa listeners de storage inválidos presos nesta aba.
  if (extensionAlive()) {
    try {
      chrome.runtime.onMessage.addListener((msg) => {
        if (!msg || !msg.type) return;
        if (msg.type === 'NFAE_PAYLOAD' && msg.payload) acceptPayload(msg.payload);
        if (msg.type === 'NFAE_PREENCHER') requestPayloadOnce().catch(() => {});
        if (msg.type === 'NFAE_WORKFLOW_COMMAND') {
          if (msg.command === 'START') { workflowActive = true; automationPaused = false; checkpoint = ''; checkpointAt = 0; }
          else if (msg.command === 'PAUSE') { workflowActive = true; automationPaused = true; checkpoint = msg.checkpoint || checkpoint || ''; }
          else if (msg.command === 'RESUME') { workflowActive = true; automationPaused = false; checkpoint = ''; checkpointAt = 0; }
          else if (msg.command === 'STOP') { workflowActive = false; automationPaused = false; checkpoint = ''; checkpointAt = 0; }
          saveWorkflowState();
          if (msg.message) updateBanner(msg.message, msg.detail || '');
          if ((msg.command === 'START' || msg.command === 'RESUME') && payload) setTimeout(() => runFill(true), 120);
          else updateBanner(automationPaused ? 'Automação pausada.' : 'Dados do Organiza carregados.');
        }
      });
    } catch (_) {}
  }


  // Algumas abas do emissor antigo apenas alternam visibilidade e não mudam
  // a árvore DOM. Um clique real do usuário em uma aba deve disparar uma nova
  // leitura da tela, sem reagir aos cliques automáticos da própria extensão.
  document.addEventListener('click', (ev) => {
    if (!payload || !ev.isTrusted || isPaused()) return;
    const el = ev.target && (ev.target.closest ? ev.target.closest('a,button,input,[onclick],[role=tab]') : null);
    if (!el || (el.closest && el.closest('#organiza-nfae-box'))) return;
    scheduleFill(420);
  }, true);

  let mutationTimer = null;
  new MutationObserver((mutations) => {
    if (!payload || isPaused()) return;

    // Ignora alterações feitas pelo próprio painel da extensão. Antes, cada
    // atualização do texto do painel acionava o observer novamente e criava
    // um loop infinito de preenchimento/pisca-pisca.
    const relevant = mutations.some((m) => {
      const target = m && m.target;
      if (!target) return false;
      const el = target.nodeType === 1 ? target : target.parentElement;
      if (el && el.closest && el.closest('#organiza-nfae-box')) return false;
      return true;
    });
    if (!relevant) return;

    if (mutationTimer) clearTimeout(mutationTimer);
    mutationTimer = setTimeout(() => {
      mutationTimer = null;
      runFill(false);
    }, 750);
  }).observe(document.documentElement, { subtree: true, childList: true });

  loadWorkflowState();
  requestPayloadOnce().catch(() => {});

  // Consulta leve apenas para receber um payload novo quando o usuário volta do
  // Organiza. Não chama preenchimento em cascata se os dados forem os mesmos.
  let payloadPolls = 0;
  payloadPollTimer = setInterval(async () => {
    if (!extensionAlive() || payloadPolls++ > 120) {
      clearInterval(payloadPollTimer);
      payloadPollTimer = null;
      return;
    }
    const answer = await runtimeMessage({type:'NFAE_GET_PAYLOAD'});
    if (answer && answer.payload) {
      const fp = fingerprint(answer.payload);
      if (fp !== payloadFingerprint) acceptPayload(answer.payload);
    }
  }, 2000);
})();
