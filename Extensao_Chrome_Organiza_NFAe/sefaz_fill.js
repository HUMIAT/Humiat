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
  let productIncludeAttemptAt = 0;
  let productListRefocusAt = 0;
  let productListRefocusCount = 0;
  let destinatarioActivated = false;
  let productWorkflowDone = false;
  let productTabActionAt = 0;
  let paymentNewOpened = false;
  let checkpointAt = 0;
  let workflowActive = false;
  let automationPaused = false;
  let checkpoint = '';
  let lastNfeSelectActionKey = '';
  let lastNfeSelectActionAt = 0;
  let nfeDestinoApplyPending = false;

  const SESSION_ACTIVE = 'organiza_nfae_workflow_active';
  const SESSION_PAUSED = 'organiza_nfae_workflow_paused';
  const SESSION_CHECKPOINT = 'organiza_nfae_workflow_checkpoint';
  const SESSION_CHECKPOINT_AT = 'organiza_nfae_workflow_checkpoint_at';
  const SESSION_PAYLOAD_FP = 'organiza_nfae_payload_fingerprint';
  const SESSION_NFE_STAGE = 'organiza_nfae_nfe_stage';
  const SESSION_NFE_STAGE_FP = 'organiza_nfae_nfe_stage_fp';
  const SESSION_HELP_DISMISSED_PREFIX = 'organiza_nfae_first_screen_help_dismissed_';
  const DEBUG_LOG_KEY = 'organiza_nfae_debug_steps_v1';
  let debugRenderTimer = null;
  let firstScreenHelpClosed = false;

  // 1.0.61: a SVRS injeta a extensão em vários frames. Apenas o frame de dados
  // confirmado pelo Console (top.frames[2]) pode comandar a automação. Os demais
  // frames continuam recebendo o payload para o popup/log, mas não podem preencher,
  // navegar nem alterar checkpoints. Isso evita reiniciar a NF-e após Pagamento.
  function isAutomationControllerFrame() {
    try {
      return !!(window.top && window.top.frames && window.top.frames.length > 2 && window.top.frames[2] === window);
    } catch (_) {
      return false;
    }
  }

  function isTopFrame() {
    try { return window.top === window; } catch (_) { return false; }
  }

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


  function loadNfeStage() {
    try {
      const fp = sessionStorage.getItem(SESSION_NFE_STAGE_FP) || '';
      if (!payloadFingerprint || fp !== payloadFingerprint) return '';
      return sessionStorage.getItem(SESSION_NFE_STAGE) || '';
    } catch (_) { return ''; }
  }

  function saveNfeStage(stage) {
    try {
      if (!payloadFingerprint) return;
      sessionStorage.setItem(SESSION_NFE_STAGE_FP, payloadFingerprint);
      if (stage) sessionStorage.setItem(SESSION_NFE_STAGE, stage);
      else sessionStorage.removeItem(SESSION_NFE_STAGE);
    } catch (_) {}
  }

  function resetNfeStage() {
    try {
      sessionStorage.removeItem(SESSION_NFE_STAGE);
      sessionStorage.removeItem(SESSION_NFE_STAGE_FP);
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

  const MAIN_ACTIVATE_EVENT = 'organiza-nfae-main-activate';
  const MAIN_CHANGE_EVENT = 'organiza-nfae-main-change';
  const MAIN_SELECT_VALUE_EVENT = 'organiza-nfae-main-select-value';
  const MAIN_FRETE_SEM_EVENT = 'organiza-nfae-main-frete-sem';
  const MAIN_PRODUCT_SEQUENCE_EVENT = 'organiza-nfae-main-product-sequence';
  const MAIN_OPEN_PAYMENT_EVENT = 'organiza-nfae-main-open-payment';
  const MAIN_TRANSPORT_SEM_EVENT = 'organiza-nfae-main-transport-sem';

  function requestMainAction(action) {
    try {
      addDebugStep('MAIN - envio', `Enviando ação ${action} ao contexto real da página.`);
      chrome.runtime.sendMessage({type:'NFAE_MAIN_ACTION', action}, (resp) => {
        try {
          if (chrome.runtime.lastError) {
            addDebugStep('MAIN - erro', `${action}: ${chrome.runtime.lastError.message || 'erro de comunicação com o background'}`);
            scheduleFill(250);
            return;
          }
          const detail = resp ? [
            `ok=${String(!!resp.ok)}`,
            `frame=${resp.frameFound === false ? 'NÃO' : 'OK'}`,
            `aba=${resp.tabFound === false ? 'NÃO' : (resp.tabActivated ? 'ATIVADA' : (resp.tabFound ? 'ENCONTRADA' : '-'))}`,
            `modo=${resp.activationMode || '-'}`,
            `FpcTabs=${resp.fpcTabsFound == null ? '-' : String(!!resp.fpcTabsFound)}`,
            `btIncDet=${resp.found == null ? '-' : (resp.found ? 'ENCONTRADO' : 'NÃO ENCONTRADO')}`,
            `clique=${resp.clicked == null ? (resp.ok ? 'OK' : '-') : (resp.clicked ? 'DISPARADO' : 'NÃO DISPARADO')}`,
            `form=${resp.formOpened == null ? '-' : (resp.formOpened ? 'ABERTO' : 'NÃO ABRIU')}`,
            resp.error ? `erro=${resp.error}` : ''
          ].filter(Boolean).join(' | ') : 'sem resposta do background';
          addDebugStep(`MAIN - retorno ${action}`, detail);
          if (resp && resp.ok) console.debug('[Organiza NFA-e] MAIN action OK:', action, resp);
          else console.debug('[Organiza NFA-e] MAIN action sem confirmação:', action, resp);

          if (action === 'PAYMENT_INCLUDE') {
            if (resp && resp.ok) {
              paymentNewOpened = true;
              // Se o contexto ainda estiver vivo, já registra o próximo estado. Se
              // a SVRS recriar a tela, pagamento_detalhe também consegue continuar.
              setCheckpoint('pagamento_preencher');
              addDebugStep('Pagamento - Incluir confirmado', `Controle=${resp.controlLabel || 'Incluir'} | hash=${resp.hash || '-'} | clique seguro confirmado.`);
              scheduleFill(650);
            } else {
              paymentNewOpened = false;
              setCheckpoint('pagamento_abrir');
              addDebugStep('Pagamento - Incluir NÃO executado', resp && resp.error ? resp.error : 'Sem confirmação do MAIN world.');
              scheduleFill(700);
            }
            return;
          }

          // 1.0.63: após o Pagamento, o ÚNICO passo final automático é clicar
          // em Validar. Não clicar em Salvar/Emitir, não abrir outra seção e
          // não responder qualquer popup da SEFAZ.
          if (action === 'VALIDATE_NOTE') {
            if (resp && resp.ok) {
              addDebugStep('Validação final', 'Botão Validar acionado. Encerrando a automação sem clicar em qualquer outro controle.');
              finishAutomation(
                'Pagamento salvo. Validar acionado.',
                'Automação encerrada após clicar somente em Validar. Salvar/Emitir e qualquer popup permanecem manuais.'
              );
            } else {
              pauseAtCheckpoint(
                'validacao_final_erro',
                'Pagamento salvo, mas não consegui clicar em Validar.',
                'Clique apenas em Validar manualmente. A extensão não continuará para nenhuma outra ação.'
              );
            }
            return;
          }

          scheduleFill(250);
        } catch (e) {
          try { addDebugStep('MAIN - callback falhou', `${action}: ${String(e && (e.message || e))}`); } catch (_) {}
        }
      });
      return true;
    } catch (e) {
      try { addDebugStep('MAIN - envio falhou', `${action}: ${String(e && (e.message || e))}`); } catch (_) {}
      return false;
    }
  }

  function activateInMainWorld(el) {
    if (!el) return false;
    // Não grava mais atributos de ACK no elemento da SEFAZ. Esses timestamps
    // faziam o campo parecer "piscar" no DevTools e não são necessários.
    try {
      el.dispatchEvent(new Event(MAIN_ACTIVATE_EVENT, { bubbles: true, cancelable: false }));
      return true;
    } catch (_) {}
    try { el.click(); return true; } catch (_) {}
    return false;
  }

  function notifyChangeInMainWorld(el) {
    if (!el) return false;
    // O listener em MAIN world executa o onchange legado da SEFAZ. Não usamos
    // atributo data-* como confirmação, para não alterar o DOM a cada tentativa.
    try {
      el.dispatchEvent(new Event(MAIN_CHANGE_EVENT, { bubbles: true, cancelable: false }));
      return true;
    } catch (_) {}
    fire(el, 'change');
    return true;
  }



  function setNfeNativeSelectOnce(el, rawValue, dispatchChange = false) {
    if (!el || el.tagName !== 'SELECT') return false;
    const raw = String(rawValue ?? '');
    if (String(el.value || '') === raw) return true;
    const opts = Array.from(el.options || []);
    const idx = opts.findIndex((o) => String(o.value || '') === raw);
    if (idx < 0) return false;
    try {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
      if (setter) setter.call(el, raw);
      else el.value = raw;
      el.selectedIndex = idx;
      if (dispatchChange) {
        // Uma única notificação ao portal. Sem focus, blur, input ou repetição.
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
      return String(el.value || '') === raw;
    } catch (_) {
      try { el.value = raw; el.selectedIndex = idx; return String(el.value || '') === raw; } catch (_) {}
    }
    return false;
  }

  function setSelectValueInMainWorld(el, rawValue) {
    if (!el || el.tagName !== 'SELECT') return false;
    const raw = String(rawValue ?? '');
    try {
      // O atributo existe apenas durante o dispatch síncrono e é removido logo
      // em seguida. O valor é aplicado no MAIN world para reproduzir a seleção
      // feita pelo usuário no próprio código legado da SEFAZ.
      el.setAttribute('data-organiza-select-target', raw);
      el.dispatchEvent(new Event(MAIN_SELECT_VALUE_EVENT, { bubbles: true, cancelable: false }));
      el.removeAttribute('data-organiza-select-target');
      return String(el.value || '') === raw;
    } catch (_) {
      try { el.removeAttribute('data-organiza-select-target'); } catch (_) {}
    }
    try {
      el.value = raw;
      fire(el, 'input');
      fire(el, 'change');
      return String(el.value || '') === raw;
    } catch (_) {}
    return false;
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

  function fieldValueEquivalent(el, value) {
    if (!el || value === undefined || value === null) return false;
    const cur = String(el.value ?? '').trim();
    const want = String(value ?? '').trim();
    if (cur === want) return true;
    if (norm(cur) === norm(want) && want !== '') return true;
    // Campos mascarados da SEFAZ (CNPJ, IE, CEP, telefone) podem exibir
    // pontuação mesmo quando o Organiza envia apenas dígitos. Nesses casos,
    // comparar pelos dígitos evita redisparar change/blur em toda rodada.
    const curDigits = digits(cur);
    const wantDigits = digits(want);
    const numericLike = wantDigits.length >= 5 && !/[a-z]/i.test(want);
    if (numericLike && curDigits === wantDigits) return true;
    return false;
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
    // Fundamental no portal legado: se o campo já está correto, NÃO tocar de
    // novo. Repetir focus/change/blur fazia a tela piscar e podia reiniciar o
    // postback, prendendo a automação no mesmo campo (principalmente IE).
    if (fieldValueEquivalent(el, v)) return true;
    try {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) desc.set.call(el, v); else el.value = v;
    } catch (_) { el.value = v; }
    fire(el, 'input');
    fire(el, 'keyup');
    fire(el, 'change');
    fire(el, 'blur');
    return fieldValueEquivalent(el, v) || String(el.value || '').length > 0;
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

  function setLegacySelectAndPostback(el, wanted) {
    if (!el || el.tagName !== 'SELECT') return false;
    const w = norm(wanted);
    if (!w) return false;
    const opts = Array.from(el.options || []);
    let opt = opts.find((o) => norm(o.textContent || '') === w || norm(o.value || '') === w);
    if (!opt) opt = opts.find((o) => norm(o.textContent || '').startsWith(w) || w.startsWith(norm(o.textContent || '')));
    if (!opt) opt = opts.find((o) => norm(o.textContent || '').includes(w) || w.includes(norm(o.textContent || '')));
    if (!opt && /^\d+$/.test(w)) {
      opt = opts.find((o) => norm(o.value || '') === w || norm(o.textContent || '').startsWith(w + ' ') || norm(o.textContent || '').startsWith(w + '-'));
    }
    if (!opt) return false;
    const changed = String(el.value || '') !== String(opt.value || '');
    if (changed) {
      el.value = opt.value;
      try { el.selectedIndex = opts.indexOf(opt); } catch (_) {}
      notifyChangeInMainWorld(el);
    }
    return isSelectAt(el, wanted);
  }

  function clickActionNearControl(control, texts) {
    if (!control) return false;
    const wanted = texts.map(norm).filter(Boolean);
    let node = control;
    for (let depth = 0; node && depth < 10; depth++, node = node.parentElement) {
      let els = [];
      try { els = Array.from(node.querySelectorAll('a,button,input[type=button],input[type=submit],[onclick],[role=button]')).filter(isVisible); } catch (_) {}
      if (!els.length) continue;
      const exact = els.find((el) => {
        const img = el.querySelector && el.querySelector('img');
        const txt = norm([el.textContent || '', el.value || '', el.title || '', img ? (img.alt || img.title || '') : ''].join(' '));
        return wanted.some((w) => txt === w);
      });
      if (exact) return activateInMainWorld(exact);
    }
    return false;
  }

  function clickByText(texts, exact = true) {
    const wanted = texts.map(norm).filter(Boolean);
    for (const doc of accessibleDocuments()) {
      let els = [];
      try { els = Array.from(doc.querySelectorAll('a,button,input[type=button],input[type=submit],[onclick],[role=button]')).filter(isVisible); } catch (_) { continue; }
      for (const el of els) {
        const img = el.querySelector && el.querySelector('img');
        const txt = norm([
          el.textContent || '', el.value || '', el.title || '',
          el.getAttribute && (el.getAttribute('aria-label') || ''),
          img ? (img.alt || img.title || '') : ''
        ].join(' '));
        if (wanted.some((w) => exact ? txt === w : (txt === w || txt.includes(w)))) {
          try { el.focus({preventScroll:true}); } catch (_) {}
          if (activateInMainWorld(el)) return true;
        }
      }
    }
    return false;
  }

  function clickAction(texts) {
    if (clickByText(texts, true)) return true;
    return clickByText(texts, false);
  }

  function findExactControlAcrossDocuments(id, name = id) {
    for (const doc of accessibleDocuments()) {
      try {
        let el = id ? doc.getElementById(id) : null;
        if (!el && name) {
          const byName = Array.from(doc.getElementsByName(name) || []);
          el = byName.find((x) => x && x.tagName) || null;
        }
        if (el) return el;
      } catch (_) {}
    }
    return null;
  }

  function clickExactLegacyControl(id, name = id) {
    // Para controles legados como btIncDet, reproduz exatamente o teste que
    // funcionou no Console: localizar o elemento real, focus() e click().
    // Evita o evento intermediário organiza-nfae-main-activate neste caso.
    let el = null;

    // 1) Documento atual do frame em que a extensão está executando.
    try {
      el = Array.from(document.querySelectorAll('input[type="button"],input[type="submit"],button,a'))
        .find((x) => String(x.id || '') === String(id || '') || String(x.name || '') === String(name || '')) || null;
    } catch (_) {}

    // 2) Caminho confirmado manualmente no portal: top.frames[2].document.
    if (!el) {
      try {
        const doc = window.top.frames[2].document;
        el = Array.from(doc.querySelectorAll('input[type="button"],input[type="submit"],button,a'))
          .find((x) => String(x.id || '') === String(id || '') || String(x.name || '') === String(name || '')) || null;
      } catch (_) {}
    }

    // 3) Fallback genérico já existente.
    if (!el) el = findExactControlAcrossDocuments(id, name);
    if (!el || el.disabled || !isVisible(el)) return false;

    // Usa o MESMO caminho que já funciona no botão Incluir/Novo do Pagamento:
    // focus() no controle real -> activateInMainWorld() -> sefaz_main.js -> el.click().
    // Não chama incluirDet() por um evento especial, porque isso podia retornar true
    // sem que o formulário do Produto tivesse realmente aberto.
    try { el.focus({ preventScroll: true }); } catch (_) { try { el.focus(); } catch (_) {} }
    return activateInMainWorld(el);
  }


  function pageBusy() {
    // O fundo cinza é normal enquanto os formulários de Produto/Pagamento estão
    // abertos; não pode ser tratado como carregamento, senão a automação nunca
    // chega a Validar/Salvar. Só considera ocupado quando há indicação real de
    // processamento/carregamento.
    const txt = bodyText();
    if (txt.includes('aguarde') && (txt.includes('processando') || txt.includes('carregando'))) return true;
    const selectors = ['.loading', '.loading-mask', '[aria-busy="true"]', '[class*="process"]', '[class*="carreg"]'];
    for (const sel of selectors) {
      let els = [];
      try { els = Array.from(document.querySelectorAll(sel)); } catch (_) {}
      for (const el of els) {
        if (!isVisible(el) || (el.closest && el.closest('#organiza-nfae-box'))) continue;
        const t = norm(el.textContent || el.title || el.getAttribute?.('aria-label') || '');
        if (t.includes('aguarde') || t.includes('process') || t.includes('carreg')) return true;
      }
    }
    return false;
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
             findSelectByOption(['COFINS 07', 'Oper. Isenta da contribuição', 'Oper Isenta da contribuicao']);
    }
    return null;
  }

  function setTaxCode(kind, code) {
    const sel = findTaxSelect(kind);
    if (!sel) return false;
    const tries = kind === 'pis'
      ? [code, 'PIS 07', 'PIS 07 - Oper. Isenta da Contribuição']
      : [code, 'COFINS 07', 'COFINS 07 - Oper. Isenta da contribuição'];
    for (const value of tries) if (setSelect(sel, value)) return true;
    return false;
  }


  function accessibleDocuments() {
    const docs = [];
    const seen = new Set();
    function visit(win) {
      if (!win || seen.has(win)) return;
      seen.add(win);
      let doc = null;
      try { doc = win.document; } catch (_) { return; }
      if (doc) docs.push(doc);
      let frames = [];
      try { frames = Array.from(win.frames || []); } catch (_) {}
      for (const fr of frames) visit(fr);
    }
    try { visit(window.top); } catch (_) { visit(window); }
    if (!docs.length) docs.push(document);
    return docs;
  }

  function clickLegacyTab(texts) {
    const wanted = texts.map(norm).filter(Boolean);
    const actionSelector = 'a,button,input[type=button],input[type=submit],input[type=image],[onclick],[href^="javascript:"],[role=tab]';

    // As abas principais da NFA-e são links normais para hashes da própria página
    // (ex.: #destinatario, #produtos, #transporte, #pagamento). Priorizar o href
    // exato é mais fiel que tentar simular o mouse pelo texto da aba.
    const hashMap = [
      { keys: ['destinatario'], hash: '#destinatario' },
      { keys: ['produtos e servicos', 'produtos'], hash: '#produtos' },
      { keys: ['transporte'], hash: '#transporte' },
      { keys: ['pagamento'], hash: '#pagamento' },
      { keys: ['observacao'], hash: '#observacao' },
      { keys: ['total'], hash: '#total' },
      { keys: ['referencias'], hash: '#referencias' },
      { keys: ['cobranca'], hash: '#cobranca' },
      { keys: ['nf e', 'nfe'], hash: '#nfe' }
    ];
    const requestedHash = (() => {
      for (const entry of hashMap) {
        if (wanted.some((w) => entry.keys.some((k) => w.includes(k) || k.includes(w)))) return entry.hash;
      }
      return '';
    })();

    if (requestedHash) {
      for (const doc of accessibleDocuments()) {
        let anchors = [];
        try {
          anchors = Array.from(doc.querySelectorAll(`a[href*="${requestedHash}"]`)).filter(isVisible);
        } catch (_) {}
        for (const action of anchors) {
          if (action.closest && action.closest('#organiza-nfae-box')) continue;
          let href = '';
          try { href = String(action.getAttribute('href') || ''); } catch (_) {}
          if (!href.toLowerCase().endsWith(requestedHash)) continue;
          try { console.debug('[Organiza NFA-e] navegação direta por hash:', requestedHash, href); } catch (_) {}
          if (activateInMainWorld(action)) return true;
        }
      }
    }

    function actionText(el) {
      const img = el.querySelector && el.querySelector('img');
      return norm([
        el.textContent || '', el.value || '', el.title || '',
        el.getAttribute && (el.getAttribute('aria-label') || ''),
        img ? (img.alt || img.title || '') : ''
      ].join(' '));
    }
    function strongMatch(txt) {
      if (!txt) return false;
      return wanted.some((w) => txt === w || txt.startsWith(w + ' ') || txt.startsWith(w + '/') || txt === w.replace(/\s+/g,' '));
    }
    function weakLeafMatch(txt) {
      if (!txt || txt.length > 80) return false;
      return wanted.some((w) => txt === w || txt.startsWith(w) || w.startsWith(txt));
    }

    for (const doc of accessibleDocuments()) {
      // 1) Primeiro tenta o próprio controle clicável. Evita o erro das versões
      // anteriores, que podiam encontrar um DIV grande contendo o texto da aba e
      // clicar no primeiro botão descendente (por exemplo Validar/Salvar).
      let direct = [];
      try { direct = Array.from(doc.querySelectorAll(actionSelector)).filter(isVisible); } catch (_) {}
      for (const action of direct) {
        if (action.closest && action.closest('#organiza-nfae-box')) continue;
        const txt = actionText(action);
        if (!strongMatch(txt)) continue;
        try { console.debug('[Organiza NFA-e] abrindo aba:', txt, action); } catch (_) {}
        if (activateInMainWorld(action)) return true;
      }

      // 2) Fallback para portais que deixam o texto em TD/SPAN e o postback no
      // pai/filho. Só aceita elementos curtos/folha para não capturar a página toda.
      let labels = [];
      try { labels = Array.from(doc.querySelectorAll('td,th,span')).filter(isVisible); } catch (_) {}
      for (const label of labels) {
        if (label.closest && label.closest('#organiza-nfae-box')) continue;
        const txt = norm(label.textContent || '');
        if (!weakLeafMatch(txt)) continue;
        let action = null;
        try {
          if (label.matches && label.matches(actionSelector)) action = label;
          if (!action && label.querySelector) {
            const child = label.querySelector(actionSelector);
            if (child && isVisible(child)) action = child;
          }
          if (!action && label.closest) {
            const parent = label.closest(actionSelector);
            if (parent && isVisible(parent)) action = parent;
          }
        } catch (_) {}
        if (!action) continue;
        try { console.debug('[Organiza NFA-e] abrindo aba via rótulo:', txt, action); } catch (_) {}
        if (activateInMainWorld(action)) return true;
      }
    }
    try { console.warn('[Organiza NFA-e] aba não localizada:', texts); } catch (_) {}
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

  function finishAutomation(message = 'Preenchimento automático concluído.', detail = 'Revise a nota. A emissão final continua sob seu controle.') {
    workflowActive = false;
    automationPaused = false;
    checkpoint = 'concluido';
    checkpointAt = Date.now();
    saveWorkflowState();
    if (fillRetryTimer) { clearTimeout(fillRetryTimer); fillRetryTimer = null; }
    if (payloadPollTimer) { clearInterval(payloadPollTimer); payloadPollTimer = null; }
    updateBanner(message, detail);
    addDebugStep('AUTOMAÇÃO ENCERRADA', 'Estado final CONCLUÍDO. Pagamento finalizado; nenhuma outra aba/seção será aberta automaticamente.');

    // 1.0.63: encerra no background somente DEPOIS do clique em Validar. Assim, se a SVRS recriar frames ou
    // abrir outra tela depois do pagamento, o mesmo payload não será entregue de
    // novo e não poderá iniciar uma segunda rotina automaticamente.
    broadcastWorkflow('STOP', {checkpoint:'concluido', message, detail}).catch(() => {});
    runtimeMessage({type:'NFAE_CLEAR_PAYLOAD'}).catch(() => {});
    payload = null;
    payloadFingerprint = '';
    return true;
  }

  function advanceProductWorkflow(kind, results) {
    if (!isWorkflowActive() || isPaused()) return false;
    const now = Date.now();

    // A SVRS abre uma janela própria "Item validado com sucesso!". O botão OK
    // precisa ser confirmado ANTES de Salvar Item; caso contrário a máscara cinza
    // continua bloqueando a janela do produto.
    if (checkpoint === 'produto_aguardando_ok' && kind.startsWith('produto_')) {
      if (clickProductValidationOk()) {
        setCheckpoint('produto_ok_confirmado');
        lastActionAt = now;
        updateBanner('Item validado com sucesso.', 'OK confirmado. Aguardando a janela liberar para salvar o item...');
        scheduleFill(750);
        return true;
      }
      if (checkpointElapsed(9000)) {
        pauseAtCheckpoint(
          'produto_ok_manual',
          'Aguardando confirmação da validação.',
          'Se aparecer "Item validado com sucesso!", clique em OK. Depois clique em Continuar automação.'
        );
        return true;
      }
      updateBanner('Validando produto...', 'Aguardando a mensagem "Item validado com sucesso!" para confirmar OK automaticamente.');
      scheduleFill(500);
      return true;
    }
    if (checkpoint === 'produto_ok_confirmado' && kind.startsWith('produto_')) {
      if (!checkpointElapsed(550) || pageBusy()) { scheduleFill(500); return true; }
      if (clickAction(['salvar item'])) {
        setCheckpoint('produto_salvando');
        lastActionAt = now;
        updateBanner('Produto validado.', 'Salvando item automaticamente...');
        scheduleFill(1200);
        return true;
      }
      if (checkpointElapsed(6500)) {
        pauseAtCheckpoint(
          'produto_salvar_manual',
          'Produto validado.',
          'Não consegui acionar Salvar Item. Salve manualmente e clique em Continuar automação.'
        );
        return true;
      }
      scheduleFill(600);
      return true;
    }
    if (checkpoint === 'produto_salvando' && kind.startsWith('produto_')) {
      if (!checkpointElapsed(8000)) { scheduleFill(900); return true; }
      pauseAtCheckpoint(
        'produto_erro',
        'A SEFAZ não retornou para a lista de produtos.',
        'Confira se há mensagem de validação. Se o item estiver salvo, feche a janela do item e clique em Continuar automação.'
      );
      return true;
    }

    if (now - productTabActionAt < 650) return false;
    let next = null;
    if (kind === 'produto_dados') {
      const essentials = ['CFOP', 'Unidade comercial', 'Valor unitário', 'Quantidade', 'Valor produto'];
      if (essentials.every((x) => resultOk(results, x))) next = ['tributos'];
      else if (!resultOk(results, 'CFOP') && productCfopRetries >= 8) {
        pauseAtCheckpoint(
          'cfop_nao_disponivel',
          'A SEFAZ não liberou um CFOP de venda nesta tela.',
          'Confira na primeira tela se o Destino está correto para a UF do cliente. RJ = Interna/5102; outra UF = Interestadual/6102. Depois clique em Continuar automação.'
        );
        return true;
      }
    } else if (kind === 'produto_icms') {
      if (resultOk(results, 'Origem') && resultOk(results, 'ICMS/CSOSN')) next = ['pis'];
    } else if (kind === 'produto_pis') {
      if (resultOk(results, 'PIS CST')) next = ['cofins'];
    } else if (kind === 'produto_cofins') {
      if (resultOk(results, 'COFINS CST')) next = ['inf adicionais', 'informações adicionais', 'informacoes adicionais'];
    } else if (kind === 'produto_adicional') {
      if (resultOk(results, 'Informações adicionais')) {
        productWorkflowDone = true;
        if (checkpoint !== 'produto_pronto_validar') {
          setCheckpoint('produto_pronto_validar');
          updateBanner('Produto preenchido.', 'Aguardando a SEFAZ concluir a última alteração antes de validar...');
          scheduleFill(900);
          return true;
        }
        if (!checkpointElapsed(700) || pageBusy()) { scheduleFill(600); return true; }
        updateBanner('Produto preenchido.', 'Validando o item. A extensão aguardará "Item validado com sucesso!", clicará em OK e só depois salvará.');
        setCheckpoint('produto_aguardando_ok');
        if (clickAction(['validar item'])) {
          lastActionAt = Date.now();
          updateBanner('Validando produto...', 'Aguardando a confirmação da SEFAZ antes de salvar.');
          scheduleFill(650);
          return true;
        }
        setCheckpoint('produto_pronto_validar');
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

  function validationSuccessContainers() {
    const found = [];
    for (const doc of accessibleDocuments()) {
      let nodes = [];
      try { nodes = Array.from(doc.querySelectorAll('div,table,td,span,fieldset')).filter(isVisible); } catch (_) { continue; }
      for (const el of nodes) {
        const t = norm(el.textContent || '');
        if (t.includes('item validado com sucesso')) found.push(el);
      }
    }
    found.sort((a, b) => String(a.textContent || '').length - String(b.textContent || '').length);
    return found;
  }

  function clickProductValidationOk() {
    for (const base of validationSuccessContainers()) {
      let node = base;
      for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
        let buttons = [];
        try { buttons = Array.from(node.querySelectorAll('a,button,input[type=button],input[type=submit],[onclick],[role=button]')).filter(isVisible); } catch (_) {}
        const ok = buttons.find((el) => {
          const img = el.querySelector && el.querySelector('img');
          const txt = norm([el.textContent || '', el.value || '', el.title || '', img ? (img.alt || img.title || '') : ''].join(' '));
          return txt === 'ok' || txt === 'sim' || txt.startsWith('ok ');
        });
        if (ok) return activateInMainWorld(ok);
      }
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

  function paymentRowCount() {
    const txt = bodyText();
    const m = txt.match(/\blinhas\s+(\d+)\b/);
    return m ? Number(m[1] || 0) : 0;
  }

  function paymentAlreadyListed() {
    return paymentRowCount() > 0 || paymentLooksSaved();
  }

  function destinatarioDadosEfetivos(d) {
    const base = Object.assign({}, (d && d.destinatario) || {});
    const rawIgual = base.endereco_entrega_igual_cliente;
    const igual = rawIgual === undefined || rawIgual === null || rawIgual === true || rawIgual === 1 || String(rawIgual).toLowerCase() === 'true' || String(rawIgual) === '1';
    const entrega = base.endereco_entrega || (d && d.endereco_entrega) || {};
    if (!igual && entrega && typeof entrega === 'object') {
      const pick = (a, b) => (a !== undefined && a !== null && String(a).trim() !== '') ? a : b;
      base.cep = pick(entrega.cep, base.cep);
      base.logradouro = pick(entrega.logradouro || entrega.endereco, base.logradouro);
      base.numero = pick(entrega.numero || entrega.endereco_numero, base.numero);
      base.complemento = pick(entrega.complemento, base.complemento);
      base.bairro = pick(entrega.bairro, base.bairro);
      base.municipio = pick(entrega.municipio || entrega.cidade, base.municipio);
      base.municipio_ibge = pick(entrega.municipio_ibge, base.municipio_ibge);
      base.uf = pick(entrega.uf || entrega.estado, base.uf);
    }
    return base;
  }

  function destinatarioLooksReady(d) {
    const x = destinatarioDadosEfetivos(d);
    const doc = digits(x.cpf_cnpj || '');
    const tipo = x.tipo_documento || (doc.length === 14 ? 'CNPJ' : doc.length === 11 ? 'CPF' : '');
    const checks = [];
    const docEl = findField(tipo === 'CPF' ? ['cpf'] : ['cnpj'], { notTypes: ['checkbox','radio'] });
    if (doc) checks.push(!!docEl && digits(docEl.value || '') === doc);
    const nomeFiscal = tipo === 'CNPJ' ? (x.razao_social || '') : (x.nome || '');
    const nomeEl = findField(['razão social nome', 'razao social nome'], { notTypes: ['checkbox','radio'] });
    if (nomeFiscal) checks.push(!!nomeEl && fieldValueEquivalent(nomeEl, nomeFiscal));
    const logr = findField(['logradouro'], { notTypes: ['checkbox','radio'] });
    if (x.logradouro) checks.push(!!logr && fieldValueEquivalent(logr, x.logradouro));
    const nro = findField(['nro', 'número', 'numero'], { notTypes: ['checkbox','radio'], reject: ['nf e','pedido','fci','recopi'] });
    if (x.numero) checks.push(!!nro && fieldValueEquivalent(nro, x.numero));
    const bairro = findField(['bairro distrito','bairro'], { notTypes: ['checkbox','radio'] });
    if (x.bairro) checks.push(!!bairro && fieldValueEquivalent(bairro, x.bairro));
    const cep = findField(['cep'], { notTypes: ['checkbox','radio'] });
    if (x.cep) checks.push(!!cep && digits(cep.value || '') === digits(x.cep));
    const ie = String(x.inscricao_estadual || '').trim();
    if (ie && ie !== '-') {
      const ieEl = findIeInput();
      checks.push(!!ieEl && digits(ieEl.value || '') === digits(ie));
    }
    const mun = findField(['município','municipio'], { tag: 'SELECT' });
    if (x.municipio && mun) {
      const selected = norm(mun.options?.[mun.selectedIndex]?.textContent || mun.value || '');
      checks.push(selected.includes(norm(x.municipio)) || norm(x.municipio).includes(selected));
    }
    return checks.length >= 6 && checks.every(Boolean);
  }

  function advanceMainWorkflow(kind, results) {
    if (!isWorkflowActive() || isPaused()) return false;
    const now = Date.now();
    if (now - lastActionAt < 650) return false;

    if (kind === 'nfe') {
      const nfeEssentials = ['Tipo de operação', 'Destino', 'UF', 'Natureza da operação', 'Consumidor final', 'Finalidade', 'Tipo atendimento', 'Intermediador'];
      if (nfeEssentials.every((x) => resultOk(results, x))) {
        if (checkpoint !== 'nfe_para_destinatario') {
          setCheckpoint('nfe_para_destinatario');
          updateBanner('Primeira tela conferida.', 'Aguardando a SEFAZ estabilizar para abrir Destinatário/Remetente...');
          scheduleFill(900);
          return true;
        }
        if (!checkpointElapsed(700) || pageBusy()) { scheduleFill(600); return true; }
        if (clickLegacyTab(['destinatário/remetente', 'destinatario/remetente', 'destinatário', 'destinatario'])) {
          setCheckpoint('destinatario_preencher');
          lastActionAt = now;
          updateBanner('Primeira tela conferida.', 'Abrindo Destinatário/Remetente automaticamente...');
          scheduleFill(900);
          return true;
        }
      }
    }

    if (kind === 'destinatario') {
      const essentials = ['Tipo documento', 'Nome/Razão Social', 'Logradouro', 'Número', 'Bairro', 'CEP', 'UF', 'Município'];
      const x = destinatarioDadosEfetivos(payload || {});
      const hasIe = !!String(x.inscricao_estadual || '').trim().replace(/^-$/, '');
      const ieReady = hasIe ? resultOk(results, 'Inscrição Estadual') : (resultOk(results, 'Sem IE') || resultOk(results, 'Não contribuinte'));
      const resultadoCompleto = essentials.every((x) => resultOk(results, x)) && ieReady;
      const telaJaCompleta = destinatarioLooksReady(payload || {});
      if (!(resultadoCompleto || telaJaCompleta)) {
        const faltando = essentials.filter((n) => !resultOk(results, n));
        if (!ieReady) faltando.push('Inscrição Estadual/Sem IE');
        try { console.debug('[Organiza NFA-e] Destinatário ainda não liberado. Faltando:', faltando, results); } catch (_) {}
        if (faltando.length) updateBanner('Destinatário preenchendo...', 'Aguardando: ' + faltando.join(', '));
      }
      if (resultadoCompleto || telaJaCompleta) {
        if (checkpoint !== 'destinatario_pronto') {
          setCheckpoint('destinatario_pronto');
          updateBanner('Destinatário preenchido.', 'Aguardando a SEFAZ concluir UF/Município...');
          scheduleFill(900);
          return true;
        }
        if (!checkpointElapsed(700) || pageBusy()) { scheduleFill(600); return true; }
        if (clickLegacyTab(['produtos e serviços', 'produtos e servicos'])) {
          setCheckpoint('produto_abrir');
          lastActionAt = now;
          updateBanner('Destinatário preenchido.', 'Abrindo Produtos e Serviços...');
          scheduleFill(850);
          return true;
        }
        updateBanner('Destinatário preenchido.', 'Os dados já estão corretos. Tentando novamente abrir a aba Produtos e Serviços, sem refazer os campos...');
        scheduleFill(800);
        return true;
      }
    }

    if (kind === 'produtos_lista') {
      if (checkpoint === 'produto_salvando' || productAlreadyListed()) {
        if (pageBusy()) { updateBanner('Produto salvo.', 'Aguardando a SEFAZ liberar a tela...'); scheduleFill(700); return true; }
        setCheckpoint('produto_salvo_lista');
        if (clickLegacyTab(['transporte'])) {
          setCheckpoint('transporte_preencher');
          lastActionAt = now;
          updateBanner('Produto salvo.', 'Seguindo para Transporte...');
          scheduleFill(850);
          return true;
        }
      }
    }

    if (kind === 'transporte' && resultOk(results, 'Modalidade frete')) {
      // Enquanto a aba Pagamento está sendo ativada, não reinicia o fluxo.
      if (checkpoint === 'pagamento_ativando') {
        scheduleFill(550);
        return true;
      }
      if (checkpoint !== 'transporte_pronto' && checkpoint !== 'pagamento_abrir') {
        setCheckpoint('transporte_pronto');
        paymentNewOpened = false;
        updateBanner('Transporte preenchido.', 'Sem frete confirmado. Preparando Pagamento...');
        scheduleFill(700);
        return true;
      }
      if (pageBusy()) { scheduleFill(500); return true; }
      if (checkpoint === 'transporte_pronto' && !checkpointElapsed(500)) { scheduleFill(400); return true; }
      if (checkpoint === 'transporte_pronto') {
        // 1.0.64: grava o próximo estado ANTES de navegar. A SVRS pode recriar o
        // contexto JavaScript ao trocar de aba; qualquer setTimeout agendado depois
        // de FpcTabs.GoTo() pode morrer junto com o documento antigo. Esse era o
        // motivo de o fluxo ficar preso em pagamento_ativando.
        setCheckpoint('pagamento_abrir');
        if (requestMainAction('OPEN_PAYMENT')) {
          lastActionAt = now;
          addDebugStep('Transporte -> Pagamento', "Checkpoint pagamento_abrir salvo. Abrindo Pagamento pelo link real focus()+click().");
          updateBanner('Transporte preenchido.', 'Sem frete confirmado. Ativando a aba Pagamento...');
          scheduleFill(1200);
          return true;
        }
        // Fallback simples caso o MAIN world não responda. Mantém pagamento_abrir,
        // pois a nova página precisa saber que está autorizada a incluir pagamento.
        if (clickLegacyTab(['pagamento'])) {
          lastActionAt = now;
          updateBanner('Transporte preenchido.', 'Abrindo Pagamento...');
          scheduleFill(800);
          return true;
        }
        setCheckpoint('transporte_pronto');
      }
      scheduleFill(600);
      return true;
    }

    if (kind === 'pagamento_detalhe') {
      const essentials = ['Meio de pagamento', 'Valor do pagamento', 'Forma de pagamento'];
      if (essentials.every((x) => resultOk(results, x))) {
        if (checkpoint !== 'pagamento_pronto' && checkpoint !== 'pagamento_salvando') {
          setCheckpoint('pagamento_pronto');
          updateBanner('Pagamento preenchido.', 'Aguardando a SEFAZ antes de salvar...');
          scheduleFill(800);
          return true;
        }
        if (checkpoint === 'pagamento_pronto') {
          if (!checkpointElapsed(650) || pageBusy()) { scheduleFill(550); return true; }
          const meioControle = findField(['meio de pagamento'], { tag: 'SELECT' });
          if (clickActionNearControl(meioControle, ['salvar'])) {
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
        if (checkpoint === 'pagamento_salvando' && checkpointElapsed(7000)) {
          pauseAtCheckpoint(
            'pagamento_erro',
            'A SEFAZ não saiu da tela do pagamento.',
            'Confira os campos obrigatórios. Corrija o que for necessário e clique em Continuar automação.'
          );
          return true;
        }
      }
    }

    if (kind === 'pagamento_lista' && (checkpoint === 'pagamento_salvando' || (/^pagamento_/.test(String(checkpoint || '')) && paymentAlreadyListed()))) {
      // 1.0.63: pagamento salvo -> clicar SOMENTE no botão global Validar.
      // Não abrir Observação, não clicar em Salvar/Emitir e não iniciar nova NF-e.
      if (checkpoint !== 'validacao_final_disparando') {
        setCheckpoint('validacao_final_disparando');
        addDebugStep('Pagamento finalizado', 'Pagamento listado. Próxima e única ação automática: clicar em Validar.');
        updateBanner('Pagamento salvo.', 'Clicando somente em Validar e encerrando a automação...');
        requestMainAction('VALIDATE_NOTE');
      }
      return true;
    }

    // Mantido apenas para uso manual: a automação 1.0.63 não navega até Observação.
    if (kind === 'observacao' && resultOk(results, 'Informações de interesse do Fisco')) {
      finishAutomation(
        'Preenchimento automático concluído.',
        'Automação encerrada. Revise a nota; Validar/Salvar/Emitir continuam sob seu controle.'
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

    // Transporte deve ter prioridade sobre textos escondidos de outras abas.
    // Na NFA-e, o conteúdo do Emitente pode permanecer no DOM mesmo com Transporte ativo.
    let freteExato = null;
    try { freteExato = document.getElementById('f_transp_modFrete'); } catch (_) {}
    if ((freteExato && isVisible(freteExato)) || hash.includes('transporte')) return 'transporte';

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

    // A aba principal ativa (hash) tem prioridade sobre texto residual de outras
    // abas. Em especial, Pagamento não pode voltar a ser lido como Produtos.
    if (hash.includes('pagamento')) return 'pagamento_lista';
    if (hash.includes('totais') || hash.includes('total')) return 'total';
    if (hash.includes('produtos')) return 'produtos_lista';

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

    const hasNovoPagamento = Array.from(document.querySelectorAll('a,button,input[type=button],input[type=submit]')).filter(isVisible).some((el) => ['novo','incluir'].includes(norm(el.textContent || el.value || el.title || '')));
    if ((txt.includes('pagamentos') && txt.includes('linhas')) || (txt.includes('pagamento') && hasNovoPagamento)) return 'pagamento_lista';
    if (txt.includes('produtos e servicos') && txt.includes('linhas')) return 'produtos_lista';
    if (txt.includes('total nota fiscal') || txt.includes('base de calculo icms')) return 'total';
    if (txt.includes('transporte')) return 'transporte';
    if (txt.includes('referencias')) return 'referencias';
    if (txt.includes('cobranca')) return 'cobranca';
    return 'outro';
  }

  function firstScreenHelpKey() {
    const fp = payloadFingerprint || (() => {
      try { return sessionStorage.getItem(SESSION_PAYLOAD_FP) || ''; } catch (_) { return ''; }
    })();
    return SESSION_HELP_DISMISSED_PREFIX + (fp || 'current');
  }

  function isFirstScreenHelpDismissed() {
    try { return sessionStorage.getItem(firstScreenHelpKey()) === '1'; }
    catch (_) { return false; }
  }

  function dismissFirstScreenHelp(permanentlyForNote = false) {
    firstScreenHelpClosed = true;
    const box = document.getElementById('organiza-nfae-first-help');
    if (box) box.remove();
    // A primeira tela pode sofrer postbacks após cada combo. Marca a ajuda como
    // confirmada nesta nota mesmo no botão Fechar, para não reaparecer a cada reload.
    try { sessionStorage.setItem(firstScreenHelpKey(), '1'); } catch (_) {}
    workflowActive = true;
    automationPaused = false;
    saveWorkflowState();
    updateBanner('Ajuda confirmada.', 'Preenchendo automaticamente a primeira tela da NF-e conforme a UF do cliente.');
    scheduleFill(150);
  }

  function showFirstScreenHelp(d) {
    if (!d || firstScreenHelpClosed || isFirstScreenHelpDismissed()) return;
    if (document.getElementById('organiza-nfae-first-help')) return;

    const destUF = String(destinatarioDadosEfetivos(d).uf || '').trim().toUpperCase();
    const destino = destUF === 'RJ' ? 'Interna' : 'Interestadual';
    const cfop = destino === 'Interestadual' ? '6102' : '5102';

    const overlay = document.createElement('div');
    overlay.id = 'organiza-nfae-first-help';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.22);z-index:2147483646;display:flex;align-items:center;justify-content:center;padding:18px;font-family:Arial,sans-serif;';
    const card = document.createElement('div');
    card.style.cssText = 'width:min(520px,92vw);background:#fff;border:1px solid #c7d5e4;border-radius:10px;box-shadow:0 12px 35px rgba(0,0,0,.28);padding:18px 20px;color:#1f2937;';
    card.innerHTML = `
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px">
        <div>
          <div style="font-size:17px;font-weight:700;color:#0f4c81">Ajuda — primeira tela da NF-e</div>
          <div style="font-size:12px;color:#607080;margin-top:3px">Cliente ${destUF || 'UF não informada'}</div>
        </div>
        <button type="button" id="organiza-nfae-help-x" title="Fechar" style="border:0;background:transparent;font-size:22px;line-height:1;cursor:pointer;color:#59636e">×</button>
      </div>
      <div style="font-size:14px;line-height:1.55;margin-bottom:12px">Confira os dados abaixo. Ao fechar, a extensão preencherá esta tela automaticamente:</div>
      <div style="background:#f4f8fc;border-left:4px solid #0f6fb5;padding:10px 12px;border-radius:5px;font-size:14px;line-height:1.65">
        <div><b>Tipo de operação:</b> Saída</div>
        <div><b>Destino:</b> ${destino}</div>
        <div><b>UF:</b> ${destUF || '-'}</div>
        <div><b>Natureza da operação:</b> Venda de Mercadoria</div>
        <div><b>Consumidor final:</b> 1 - Sim</div>
        <div><b>Finalidade:</b> 1 - NF-e normal</div>
        <div><b>Tipo atendimento:</b> 2 - Não presencial pela Internet</div>
        <div><b>Intermediador:</b> 0 - Sem intermediador</div>
        <div style="margin-top:6px"><b>CFOP esperado no produto:</b> ${cfop}</div>
      </div>
      <div style="font-size:12px;color:#5d6772;margin-top:10px">A extensão preencherá um combo por vez, aguardando os postbacks da SEFAZ, e depois seguirá para Destinatário/Remetente.</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;margin-top:15px">
        <button type="button" id="organiza-nfae-help-hide-note" style="padding:7px 10px;border:1px solid #b7c2cc;background:#fff;border-radius:5px;cursor:pointer">Não mostrar novamente nesta nota</button>
        <button type="button" id="organiza-nfae-help-close" style="padding:7px 14px;border:1px solid #0f6fb5;background:#0f6fb5;color:#fff;border-radius:5px;cursor:pointer;font-weight:700">OK, preencher automaticamente</button>
      </div>`;
    overlay.appendChild(card);
    document.documentElement.appendChild(overlay);

    card.querySelector('#organiza-nfae-help-x').addEventListener('click', () => dismissFirstScreenHelp(false));
    card.querySelector('#organiza-nfae-help-close').addEventListener('click', () => dismissFirstScreenHelp(false));
    card.querySelector('#organiza-nfae-help-hide-note').addEventListener('click', () => dismissFirstScreenHelp(true));
    overlay.addEventListener('click', (ev) => { if (ev.target === overlay) dismissFirstScreenHelp(false); });
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

  function exactSelect(id, fallback = null, indexFallback = null) {
    // Nesta tela antiga da SVRS, getElementById() pode retornar null mesmo com
    // o SELECT visível no DOM. O teste manual confirmou que querySelectorAll
    // enxerga os combos corretamente. Procura por id/name percorrendo os SELECTs
    // e, para a primeira tela, usa também o índice confirmado no Console.
    let selects = [];
    try { selects = Array.from(document.querySelectorAll('select')); } catch (_) {}
    const ids = [String(id || '')];
    if (String(id || '').startsWith('_')) ids.push(String(id).slice(1));
    else if (id) ids.push('_' + String(id));
    const el = selects.find((sel) => ids.includes(String(sel.id || '')) || ids.includes(String(sel.name || '')));
    if (el && el.tagName === 'SELECT') return el;
    if (Number.isInteger(indexFallback) && selects[indexFallback] && selects[indexFallback].tagName === 'SELECT') {
      return selects[indexFallback];
    }
    return typeof fallback === 'function' ? fallback() : null;
  }

  function nfeExpectedRawValue(el, wanted) {
    if (!el) return '';
    const id = String(el.id || el.name || '');
    const w = norm(wanted);
    if (id === '_f_tpNF') {
      if (w === 'saida') return '1';
      if (w === 'entrada') return '0';
    }
    if (id === '_f_idDest') {
      if (w === 'interna') return '1';
      if (w === 'interestadual') return '2';
      if (w === 'exterior') return '3';
    }
    return '';
  }

  function nfeSelectAt(el, wanted) {
    if (!el || el.tagName !== 'SELECT') return false;
    const raw = nfeExpectedRawValue(el, wanted);
    if (raw && String(el.value || '') === raw) return true;
    return isSelectAt(el, wanted);
  }

  function setNfeExactStep(label, el, wanted, waitMs = 1050) {
    if (!el || !wanted) return false;
    if (nfeSelectAt(el, wanted)) return true;

    // Se a SEFAZ ainda estiver processando exatamente este mesmo combo, apenas
    // aguarda. Não redispara change em loop. Isso era o que fazia _f_tpNF
    // receber novos ACKs continuamente e impedia a passagem para _f_idDest.
    const actionKey = `${String(el.id || el.name || label)}=${norm(wanted)}`;
    const now = Date.now();
    if (actionKey === lastNfeSelectActionKey && now - lastNfeSelectActionAt < 2800) {
      updateBanner('Aguardando a SEFAZ...', `${label}: ${wanted}. Não vou reenviar o mesmo campo enquanto a tela atualiza.`);
      scheduleFill(650);
      return false;
    }

    updateBanner('Preenchendo NF-e...', `${label}: ${wanted}. Aguardando a SEFAZ liberar o próximo campo.`);
    const raw = nfeExpectedRawValue(el, wanted);
    let changed = false;
    if (raw && Array.from(el.options || []).some((o) => String(o.value) === raw)) {
      if (String(el.value || '') !== raw) {
        el.value = raw;
        changed = true;
      }
    } else {
      const before = String(el.value || '');
      changed = !!setLegacySelectAndPostback(el, wanted) && before !== String(el.value || '');
      if (changed) {
        lastNfeSelectActionKey = actionKey;
        lastNfeSelectActionAt = now;
        lastActionAt = now;
        scheduleFill(waitMs);
        return false;
      }
    }

    if (changed) {
      lastNfeSelectActionKey = actionKey;
      lastNfeSelectActionAt = now;
      notifyChangeInMainWorld(el);
      lastActionAt = now;
      scheduleFill(waitMs);
      return false;
    }

    // Se o valor já ficou correto durante a atualização assíncrona, segue sem
    // disparar outro evento.
    if (nfeSelectAt(el, wanted)) return true;
    updateBanner('Não consegui preencher um campo da NF-e.', `${label}: selecione ${wanted} manualmente e a automação continuará.`);
    return false;
  }

  function setSelectIfDifferent(el, wanted) {
    if (!el) return false;
    if (isSelectAt(el, wanted)) return true;
    return setSelect(el, wanted);
  }

  function fillNfe(d) {
    showFirstScreenHelp(d);
    const results = [];
    const destUF = String(destinatarioDadosEfetivos(d).uf || '').trim().toUpperCase();
    const destinoEsperado = destUF === 'RJ' ? 'Interna' : 'Interestadual';
    const destinoRaw = destUF === 'RJ' ? '1' : '2';

    const tipoOp = exactSelect('_f_tpNF', () => findSelectWithOptions(['Entrada', 'Saída']), 0);
    const destino = exactSelect('_f_idDest', () => findSelectWithOptions(['Interna', 'Interestadual']), 1);

    if (!firstScreenHelpClosed && !isFirstScreenHelpDismissed()) {
      const cfopEsperado = destinoRaw === '2' ? '6102' : '5102';
      updateBanner('Confira a ajuda da primeira tela.', `Cliente ${destUF || '-'} / ${destinoEsperado}. CFOP esperado: ${cfopEsperado}.`);
      return results;
    }

    // Não reativa o workflow a partir desta tela. A ativação só pode vir do
    // início explícito do fluxo no frame controlador.
    if (!isWorkflowActive()) return results;

    if (!destUF) {
      updateBanner('UF do cliente não informada.', 'Não consigo decidir entre Interna e Interestadual sem a UF do destinatário.');
      return results;
    }

    // Fluxo confirmado manualmente no Console da própria SEFAZ:
    // em TODOS os quatro combos: focus() -> value -> UM change.
    // A SEFAZ precisa processar cada change antes do próximo campo.
    // Índices confirmados: 0=Saída, 1=Destino, 3=UF, 4=Natureza.
    // Não usar TAB, blur, input nem repetir o mesmo evento em loop.

    // 1) Tipo de operação: Saída. focus + value=1 + UM change.
    if (!tipoOp) {
      updateBanner('Aguardando a primeira tela da NF-e...', 'Campo Tipo de operação (_f_tpNF) ainda não apareceu.');
      scheduleFill(450);
      return results;
    }
    if (String(tipoOp.value || '') !== '1') {
      addDebugStep('NF-e 1/4 - Tipo de operação', `Antes=${String(tipoOp.value || '-')} | focus + 1 (Saída) + 1 change`);
      try {
        tipoOp.focus();
        tipoOp.value = '1';
        tipoOp.dispatchEvent(new Event('change', { bubbles: true }));
      } catch (_) {}
      const ok = String(tipoOp.value || '') === '1';
      addDebugStep('NF-e 1/4 - Tipo de operação enviado', `resultado=${ok ? 'OK' : 'FALHOU'} | agora=${String(tipoOp.value || '-')} | aguardando a SEFAZ processar`);
      if (!ok) {
        updateBanner('Não consegui definir Tipo de operação.', 'Selecione Saída manualmente; a automação continuará.');
        return results;
      }
      saveNfeStage('saida_change_sent');
      updateBanner('Preenchendo NF-e...', '1/4 Saída definida. Aguardando a SEFAZ liberar/atualizar Destino.');
      scheduleFill(900);
      return results;
    }
    results.push(['Tipo de operação', true]);

    // 2) Destino. O teste no Console confirmou que value + UM change é suficiente.
    if (!destino) {
      updateBanner('Saída definida.', 'Aguardando o campo Tipo de operação (Destino) (_f_idDest).');
      scheduleFill(400);
      return results;
    }
    if (String(destino.value || '') !== destinoRaw) {
      addDebugStep('NF-e 2/4 - Destino', `Antes=${String(destino.value || '-')} | aplicando ${destinoRaw} (${destinoEsperado}) | depois: 1 change`);
      const opts = Array.from(destino.options || []);
      const existe = opts.some((o) => String(o.value || '') === destinoRaw);
      if (!existe) {
        updateBanner('Destino ainda não disponível.', `O combo não possui o valor ${destinoRaw} (${destinoEsperado}).`);
        scheduleFill(500);
        return results;
      }
      try {
        // Repete exatamente o teste que funcionou no Console: focus + value + change.
        destino.focus();
        destino.value = destinoRaw;
        destino.dispatchEvent(new Event('change', { bubbles: true }));
      } catch (_) {}
      const ok = String(destino.value || '') === destinoRaw;
      addDebugStep('NF-e 2/4 - Destino enviado', `resultado=${ok ? 'OK' : 'FALHOU'} | agora=${String(destino.value || '-')} | aguardando a SEFAZ liberar UF`);
      if (!ok) {
        updateBanner('Não consegui definir Destino.', `Selecione ${destinoEsperado} manualmente; a automação continuará.`);
        return results;
      }
      saveNfeStage('destino_change_sent');
      updateBanner('Preenchendo NF-e...', `2/4 ${destinoEsperado} definido. Aguardando a SEFAZ liberar UF.`);
      scheduleFill(900);
      return results;
    }
    results.push(['Destino', true]);
    if (loadNfeStage() !== 'destino_done') saveNfeStage('destino_done');

    // 3) UF. Somente operação interestadual possui esta etapa.
    // Para RJ/Interna, não tenta localizar nem preencher UF; segue direto para Natureza.
    if (destinoRaw === '2') {
      const uf = exactSelect('_f_dest_enderDest_UF', () => findUfSelect(), 3);
      if (!uf) {
        updateBanner('Destino confirmado.', `Aguardando o campo UF ser liberado para selecionar ${destUF}.`);
        scheduleFill(450);
        return results;
      }
      const optUf = Array.from(uf.options || []).find((o) => String(o.value || '').toUpperCase() === destUF || norm(o.textContent || '') === norm(destUF));
      if (!optUf) {
        updateBanner('UF ainda não disponível.', `Aguardando ${destUF} aparecer no combo.`);
        scheduleFill(500);
        return results;
      }
      if (String(uf.value || '').toUpperCase() !== String(optUf.value || '').toUpperCase()) {
        addDebugStep('NF-e 3/4 - UF', `Antes=${String(uf.value || '-')} | focus + ${String(optUf.value || destUF)} (${destUF}) + 1 change`);
        try {
          uf.focus();
          uf.value = String(optUf.value || destUF);
          uf.dispatchEvent(new Event('change', { bubbles: true }));
        } catch (_) {}
        const ok = String(uf.value || '').toUpperCase() === String(optUf.value || '').toUpperCase();
        addDebugStep('NF-e 3/4 - UF enviada', `resultado=${ok ? 'OK' : 'FALHOU'} | agora=${String(uf.value || '-')} | aguardando opções da Natureza`);
        if (!ok) {
          updateBanner('Não consegui definir UF.', `Selecione ${destUF} manualmente; a automação continuará.`);
          return results;
        }
        saveNfeStage('uf_change_sent');
        updateBanner('Preenchendo NF-e...', `3/4 UF ${destUF} definida. Aguardando a SEFAZ carregar Natureza.`);
        scheduleFill(900);
        return results;
      }
      results.push(['UF', true]);
    } else {
      results.push(['UF (não aplicável)', true]);
      if (loadNfeStage() !== 'uf_skipped_internal') {
        addDebugStep('NF-e 3/4 - UF', 'Venda interna RJ: UF não é preenchida nesta etapa.');
        saveNfeStage('uf_skipped_internal');
      }
    }

    // 4) Natureza da operação. focus + value=1 + UM change, como confirmado no Console.
    const natureza = exactSelect('_f_cNatureza', () => findNaturezaSelect(), destinoRaw === '2' ? 4 : null);
    if (!natureza) {
      updateBanner('UF definida.', 'Aguardando o campo Natureza da operação (_f_cNatureza).');
      scheduleFill(400);
      return results;
    }
    const vendaPorValor = Array.from(natureza.options || []).find((o) => String(o.value || '') === '1');
    const vendaPorTexto = Array.from(natureza.options || []).find((o) => norm(o.textContent || '').includes('venda de mercadoria'));
    const venda = vendaPorValor || vendaPorTexto;
    if (!venda) {
      updateBanner('Natureza ainda não disponível.', 'A opção Venda de Mercadoria ainda não apareceu no combo.');
      scheduleFill(450);
      return results;
    }
    if (String(natureza.value || '') !== String(venda.value || '')) {
      addDebugStep('NF-e 4/4 - Natureza', `Antes=${String(natureza.value || '-')} | focus + ${String(venda.value || '1')} (Venda de Mercadoria) + 1 change`);
      try {
        natureza.focus();
        natureza.value = String(venda.value || '1');
        natureza.dispatchEvent(new Event('change', { bubbles: true }));
      } catch (_) {}
      const ok = String(natureza.value || '') === String(venda.value || '');
      addDebugStep('NF-e 4/4 - Natureza enviada', `resultado=${ok ? 'OK' : 'FALHOU'} | agora=${String(natureza.value || '-')} | aguardando a SEFAZ processar`);
      if (!ok) {
        updateBanner('Não consegui definir Natureza.', 'Selecione Venda de Mercadoria manualmente; a automação continuará.');
        return results;
      }
      saveNfeStage('natureza_change_sent');
      updateBanner('Primeira etapa concluída.', 'Saída, Destino, UF e Natureza foram definidos com a sequência confirmada no Console.');
      scheduleFill(900);
      return results;
    }
    results.push(['Natureza da operação', true]);
    saveNfeStage('natureza_done');

    // Estes campos já estavam funcionando. Mantém a lógica anterior sem alteração.
    const consumidor = findField(['consumidor final'], { tag: 'SELECT' });
    const finalidade = findField(['finalidade de emissão', 'finalidade de emissao'], { tag: 'SELECT' });
    const atendimento = findField(['tipo atendimento'], { tag: 'SELECT' });
    const intermediador = findField(['ind intermediador marketplace', 'intermediador marketplace'], { tag: 'SELECT' });

    if (consumidor) results.push(['Consumidor final', setSelectIfDifferent(consumidor, '1 - Sim')]);
    if (finalidade) results.push(['Finalidade', setSelectIfDifferent(finalidade, '1 - NF-e normal')]);
    if (atendimento) results.push(['Tipo atendimento', setSelectIfDifferent(atendimento, '2 - Operação NÃO Presencial, pela INTERNET')]);
    if (intermediador) results.push(['Intermediador', setSelectIfDifferent(intermediador, '0 - Operação sem intermediador')]);

    return results;
  }

  function findSemIeCheckbox() {
    // Checkbox legado "Sem Inscrição Estadual". Esta função era chamada
    // pelo fluxo do destinatário, mas não estava definida, interrompendo o
    // preenchimento com ReferenceError. Localiza primeiro pelo rótulo e depois
    // pelo contexto da linha/célula para tolerar o HTML antigo da SVRS.
    const byLabel = findField(
      ['sem inscrição estadual', 'sem inscricao estadual', 'sem ie'],
      { type: 'checkbox' }
    );
    if (byLabel && String(byLabel.type || '').toLowerCase() === 'checkbox') return byLabel;

    const boxes = Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(isVisible);
    for (const box of boxes) {
      const meta = fieldMeta(box);
      if (meta.includes('sem inscricao estadual') || meta.includes('sem ie')) return box;
      const row = box.closest && (box.closest('tr') || box.closest('td'));
      const txt = norm(row && row.textContent || '');
      if (txt.includes('sem inscricao estadual') || txt.includes('sem ie')) return box;
    }
    return null;
  }

  function findIeInput() {
    const byLabel = findField(['inscrição estadual', 'inscricao estadual'], { notTypes: ['checkbox', 'radio'], reject: ['st'] });
    if (byLabel && byLabel.tagName === 'INPUT' && String(byLabel.type || 'text').toLowerCase() !== 'checkbox') return byLabel;

    // Na tela da SVRS o input da IE fica imediatamente antes do checkbox
    // "Sem Inscrição Estadual". Usa essa estrutura como fallback, pois os
    // rótulos antigos nem sempre têm for/id relacionados ao campo.
    const box = findSemIeCheckbox();
    const row = box && box.closest ? box.closest('tr') : null;
    if (row) {
      const all = Array.from(row.querySelectorAll('input:not([type=hidden]),select')).filter(isVisible);
      const idx = all.indexOf(box);
      for (let i = idx - 1; i >= 0; i--) {
        const el = all[i];
        const type = String(el.type || '').toLowerCase();
        if (el.tagName === 'INPUT' && !['checkbox','radio','button','submit'].includes(type)) return el;
      }
    }
    return byLabel || null;
  }

  function fillDestinatario(d) {
    const x = destinatarioDadosEfetivos(d);
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
    const situacaoIcms = String(x.situacao_icms || '').trim().toUpperCase();
    const temIe = !!ie && ie !== '-';
    const semIe = !temIe;
    const semIeBox = findSemIeCheckbox();
    if (semIeBox) setValue(semIeBox, semIe);
    results.push(['Sem IE', semIe ? !!(semIeBox && semIeBox.checked) : !!(semIeBox && !semIeBox.checked)]);

    if (temIe) {
      const ieInput = findIeInput();
      const ieJaCorreta = !!ieInput && digits(ieInput.value || '') === digits(ie);
      // Só ativa o campo quando realmente precisa alterá-lo. Antes, a extensão
      // focava a IE em toda rodada e o cursor ficava piscando indefinidamente.
      if (ieInput && !ieJaCorreta) activateLegacyControl(ieInput);
      const ieOk = ieJaCorreta || setValue(ieInput, ie);
      results.push(['Inscrição Estadual', ieOk && digits(ieInput && ieInput.value) === digits(ie)]);
    }

    // Não deduz contribuinte apenas pela existência de IE. O Organiza envia a
    // situação fiscal confirmada separadamente (CONTRIBUINTE, NÃO CONTRIBUINTE
    // ou ISENTO), inclusive para os casos raros de não contribuinte com IE.
    if (situacaoIcms === 'CONTRIBUINTE') {
      const contrib = findSelectByOption(['contribuinte icms', 'contribuinte']);
      results.push(['Situação ICMS', contrib ? setSelect(contrib, 'CONTRIBUINTE') : false]);
    } else if (situacaoIcms === 'NAO_CONTRIBUINTE') {
      const contrib = findSelectByOption(['não contribuinte', 'nao contribuinte']);
      results.push(['Situação ICMS', contrib ? setSelect(contrib, 'NÃO CONTRIBUINTE') : false]);
    } else if (situacaoIcms === 'ISENTO') {
      const contrib = findSelectByOption(['isento']);
      results.push(['Situação ICMS', contrib ? setSelect(contrib, 'ISENTO') : false]);
    } else {
      results.push(['Situação ICMS', false]);
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

    // Os três campos iniciais continuam manuais, mas a extensão valida o Destino
    // antes de chegar aqui. Assim o CFOP esperado pelo Organiza deve estar entre
    // as opções liberadas pela SEFAZ: 5102 para RJ e 6102 para outra UF.
    let cfopOk = false;
    if (cfop) {
      try { cfop.focus({preventScroll: true}); } catch (_) { try { cfop.focus(); } catch (_) {} }
      const available = Array.from(cfop.options || []);
      const hasCode = (code) => available.some((o) => {
        const t = norm(`${o.value || ''} ${o.textContent || ''}`);
        return t === norm(code) || t.startsWith(norm(code) + ' ') || t.includes(norm(code) + ' venda');
      });
      const ufDest = String(destinatarioDadosEfetivos(d).uf || '').trim().toUpperCase();
      const preferred = String(p.cfop || (ufDest === 'RJ' ? '5102' : '6102')).trim();
      // O Organiza calcula o CFOP esperado pela UF. Se a SEFAZ não disponibilizar
      // esse código, não escolhe o oposto: volta a orientar a primeira tela.
      if (preferred && hasCode(preferred)) cfopOk = setSelect(cfop, preferred);
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
    results.push(['ICMS/CSOSN', setByLabel(['tributação icms cst csosn', 'tributacao icms cst csosn'], p.csosn || '102', { tag: 'SELECT' }).ok]);
    // Regime vem da SEFAZ (Simples Nacional - MEI) e valores permanecem vazios/zero.
    return results;
  }

  function fillProdutoPis(d) {
    const p = d.produto || {};
    return [['PIS CST', setTaxCode('pis', p.pis_cst || '07')]];
  }

  function fillProdutoCofins(d) {
    const p = d.produto || {};
    return [['COFINS CST', setTaxCode('cofins', p.cofins_cst || '07')]];
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
    let n = norm(forma);
    // 'Histórico' é apenas o marcador legado do Organiza quando não existe um
    // lançamento de pagamento detalhado; no padrão fiscal já validado pelo
    // usuário, esse caso é tratado como Pix em vez de enviar 'Histórico'.
    if (!n || n === 'historico') n = 'pix';
    let descricao = (n === 'pix' ? 'Pix' : forma) || 'Outros';
    let meio = ['99 - Outros', '99', 'Outros'];
    if (n.includes('pix')) { descricao = 'Pix'; meio = ['99 - Outros', '99', 'Outros']; }
    else if (n.includes('credito')) { descricao = 'Cartão de Crédito'; meio = ['03', 'Cartão de Crédito']; }
    else if (n.includes('debito')) { descricao = 'Cartão de Débito'; meio = ['04', 'Cartão de Débito']; }
    else if (n.includes('dinheiro')) { descricao = 'Dinheiro'; meio = ['01', 'Dinheiro']; }
    else if (n.includes('boleto')) { descricao = 'Boleto'; meio = ['15', 'Boleto']; }
    const valor = Number(explicit.valor || first.valor || (d.totais || {}).recebido || (d.totais || {}).venda || 0);
    return { descricao, meio, valor };
  }

  function setLegacySelectByCode(el, code, aliases = []) {
    if (!el || el.tagName !== 'SELECT') return false;
    const wantedCode = norm(code);
    const wantedAliases = aliases.map(norm).filter(Boolean);
    const opts = Array.from(el.options || []);
    let opt = opts.find((o) => norm(o.value || '') === wantedCode);
    if (!opt) opt = opts.find((o) => {
      const t = norm(`${o.value || ''} ${o.textContent || ''}`);
      return t === wantedCode || t.startsWith(wantedCode + ' ') || t.startsWith(wantedCode + '-') || wantedAliases.some((a) => t.includes(a));
    });
    if (!opt) return false;
    const changed = String(el.value || '') !== String(opt.value || '');
    if (changed) {
      el.value = opt.value;
      try { el.selectedIndex = opts.indexOf(opt); } catch (_) {}
      // O combo de Transporte possui lógica de página legada. Dispara o change
      // no MAIN world para que o AutoPostBack/onchange da SEFAZ seja executado.
      notifyChangeInMainWorld(el);
    }
    const selected = norm(`${el.value || ''} ${(el.options && el.selectedIndex >= 0 && el.options[el.selectedIndex]) ? el.options[el.selectedIndex].textContent : ''}`);
    return selected === wantedCode || selected.startsWith(wantedCode + ' ') || selected.startsWith(wantedCode + '-') || wantedAliases.some((a) => selected.includes(a));
  }

  function fillTransporte(d) {
    const results = [];
    // Campo confirmado na SEFAZ:
    // <select name="f_transp_modFrete" id="f_transp_modFrete"> ... <option value="9">9 - Sem frete</option>
    // Reproduz exatamente o teste que funcionou no Console:
    // focus() -> value='9' -> change.
    let modalidade = null;

    // 1) Procura primeiro no documento atual, percorrendo os SELECTs como no Console.
    try {
      modalidade = Array.from(document.querySelectorAll('select'))
        .find((el) => String(el.id || '') === 'f_transp_modFrete' || String(el.name || '') === 'f_transp_modFrete') || null;
    } catch (_) {}

    // 2) Caminho confirmado manualmente: top.frames[2].document.
    if (!modalidade) {
      try {
        const doc = window.top.frames[2].document;
        modalidade = Array.from(doc.querySelectorAll('select'))
          .find((el) => String(el.id || '') === 'f_transp_modFrete' || String(el.name || '') === 'f_transp_modFrete') || null;
      } catch (_) {}
    }

    // 3) Fallbacks para variações do portal.
    if (!modalidade) modalidade = findExactControlAcrossDocuments('f_transp_modFrete', 'f_transp_modFrete');
    if (!modalidade) modalidade = findExactControlAcrossDocuments('_f_transp_modFrete', '_f_transp_modFrete');
    if (!modalidade) modalidade = findField(['modalidade frete', 'modalidade de frete'], { tag: 'SELECT' });
    if (!modalidade) modalidade = findSelectByOption(['9 - Sem frete', 'Sem frete']);

    let ok = false;
    if (modalidade && modalidade.tagName === 'SELECT') {
      const opts = Array.from(modalidade.options || []);
      const semFrete = opts.find((o) => String(o.value || '') === '9');
      if (semFrete) {
        const before = String(modalidade.value || '-');
        if (String(modalidade.value || '') !== '9') {
          // Executa no MAIN world real do frame confirmado, igual ao Console.
          requestMainAction('TRANSPORT_SEM_FRETE');
          if (String(modalidade.value || '') !== '9') {
            try { modalidade.dispatchEvent(new Event(MAIN_FRETE_SEM_EVENT, { bubbles: true, cancelable: false })); } catch (_) {}
          }
        }
        ok = String(modalidade.value || '') === '9';
        addDebugStep(
          'Transporte - Modalidade Frete',
          `Antes=${before} | focus + 9 (Sem frete) + 1 change | agora=${String(modalidade.value || '-')} | resultado=${ok ? 'OK' : 'FALHOU'}`
        );
      }
    }

    // Se a SEFAZ ainda não expôs o combo/valor, mantém a rotina viva para tentar
    // novamente em vez de parar silenciosamente nesta tela.
    if (!ok) scheduleFill(700);

    results.push(['Modalidade frete', ok]);
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
    const elapsedSinceAction = now - lastActionAt;
    if (elapsedSinceAction < 1600) {
      // 1.0.60: ao chegar em Produtos, o preenchimento era reexecutado ~850 ms
      // depois do clique da aba, mas este bloqueio exigia 1600 ms. Assim a tentativa
      // de btIncDet nem era enviada e nenhum log de clique aparecia. Agenda uma nova
      // execução após o cooldown em vez de simplesmente abandonar a tentativa.
      if (kind === 'produtos_lista' && checkpoint === 'produto_abrir') {
        const wait = Math.max(180, 1650 - elapsedSinceAction);
        addDebugStep('Produtos - aguardando clique', `Ainda no cooldown da troca de aba (${elapsedSinceAction} ms/1600 ms). Nova tentativa em ${wait} ms.`);
        scheduleFill(wait);
      }
      if (kind === 'pagamento_lista' && checkpoint === 'pagamento_abrir') {
        const wait = Math.max(180, 1650 - elapsedSinceAction);
        addDebugStep('Pagamento - aguardando ação', `Ainda no cooldown da troca de aba (${elapsedSinceAction} ms/1600 ms). Nova tentativa em ${wait} ms.`);
        scheduleFill(wait);
      }
      return false;
    }
    const txt = bodyText();
    if (kind === 'produtos_lista') {
      // Só é permitido abrir um NOVO produto quando o fluxo acabou de chegar
      // oficialmente do Destinatário. Depois de Transporte/Pagamento nunca
      // volta a disparar btIncDet, mesmo que texto residual de Produtos exista.
      if (checkpoint !== 'produto_abrir') return false;
      const p = (payload && payload.produto) || {};
      const code = norm(p.codigo || '');
      const desc = norm(p.descricao || '');
      const alreadyListed = (code && txt.includes(code)) || (desc && txt.includes(desc));
      const looksEmpty = txt.includes('nao ha item') || txt.includes('nenhum item') || txt.includes('nenhum produto');
      // Se uma tentativa foi enviada mas o formulário não abriu, libera nova tentativa.
      // Antes a flag ficava travada em true mesmo quando o clique não surtia efeito.
      if (productIncludeOpened && productIncludeAttemptAt && (now - productIncludeAttemptAt) > 1800) {
        productIncludeOpened = false;
    productIncludeAttemptAt = 0;
      }
      if (!alreadyListed && !productIncludeOpened && (looksEmpty || txt.includes('produtos e servicos'))) {
        // A função real do listener da aba foi descoberta no DevTools: FpcTabs.GoTo().
        // A 1.0.60 executa no MAIN world a sequência confirmada no Console: link da aba -> focus/click -> espera -> btIncDet focus/click.
        if (!productIncludeOpened || !productIncludeAttemptAt || (now - productIncludeAttemptAt) > 2200) {
          if (requestMainAction('PRODUCT_INCLUDE_SEQUENCE')) {
            productIncludeOpened = true;
            productIncludeAttemptAt = now;
            productListRefocusAt = now;
            productListRefocusCount += 1;
            lastActionAt = now;
            addDebugStep('Produtos - ativação', `Sequência MAIN enviada: aba Produtos focus()+click() -> btIncDet focus()+click() (tentativa ${productListRefocusCount}). Aguardando retorno detalhado.`);
            updateBanner('Produtos e Serviços', 'Ativando a própria aba Produtos e Serviços antes do Incluir...');
            scheduleFill(1700);
            return true;
          }
        }

        // Fallback: botão exato, caso o portal já esteja corretamente ativado.
        if (clickExactLegacyControl('btIncDet', 'btIncDet')) {
          productIncludeOpened = true;
          productIncludeAttemptAt = now;
          lastActionAt = now;
          addDebugStep('Produtos - Incluir', 'Fallback: btIncDet acionado diretamente após tentativa da sequência MAIN world.');
          scheduleFill(900);
          return true;
        }
        // Fallback para variações futuras da página.
        if (clickAction(['incluir'])) {
          productIncludeOpened = true;
          productIncludeAttemptAt = now;
          lastActionAt = now;
          addDebugStep('Produtos - Incluir', 'Botão Incluir acionado por fallback de texto. Aguardando o formulário abrir.');
          try { console.debug('[Organiza NFA-e] Produtos: botão Incluir acionado por fallback.'); } catch (_) {}
          scheduleFill(900);
          return true;
        }
        try { console.debug('[Organiza NFA-e] Produtos: btIncDet/Incluir ainda não localizado/acionado.'); } catch (_) {}
      }
    }
    if (kind === 'pagamento_lista') {
      // Recuperação defensiva: versões anteriores podiam morrer durante a troca de
      // aba deixando o checkpoint em pagamento_ativando. Ao já estar na lista de
      // Pagamento, esse estado equivale com segurança a pagamento_abrir.
      if (checkpoint === 'pagamento_ativando') {
        setCheckpoint('pagamento_abrir');
        addDebugStep('Pagamento - recuperação', 'Checkpoint antigo pagamento_ativando convertido para pagamento_abrir na própria aba Pagamento.');
      }
      // Não cria outra linha se a lista já possui pagamento.
      if (paymentAlreadyListed()) {
        paymentNewOpened = true;
        return false;
      }
      // Abrir a aba Pagamento NÃO autoriza criar uma linha. O botão Incluir/Novo
      // só pode ser acionado quando o fluxo chegou oficialmente do Transporte.
      const precisaAbrir = checkpoint === 'pagamento_abrir' && !paymentAlreadyListed();
      if (precisaAbrir && !paymentNewOpened) {
        // 1.0.64: NÃO usa mais clickAction(['novo','incluir']). Essa função varria
        // todos os frames/documentos e podia acertar um controle "Novo" fora do
        // Pagamento, inclusive iniciando outra NFA-e. O clique agora é executado no
        // MAIN world, exclusivamente em top.frames[2].document e apenas no controle
        // de texto exato "Incluir" da aba Pagamento.
        setCheckpoint('pagamento_incluindo');
        paymentNewOpened = true; // trava imediata contra repetição enquanto aguarda ACK
        if (requestMainAction('PAYMENT_INCLUDE')) {
          lastActionAt = now;
          addDebugStep('Pagamento - Incluir', 'Solicitado clique seguro no Incluir da própria aba Pagamento (top.frames[2] apenas).');
          updateBanner('Pagamento', 'Acionando Incluir somente dentro da aba Pagamento...');
          scheduleFill(1400);
          return true;
        }
        paymentNewOpened = false;
        setCheckpoint('pagamento_abrir');
        addDebugStep('Pagamento - Incluir falhou', 'Não foi possível enviar a ação PAYMENT_INCLUDE; nenhuma busca global por Novo/Incluir foi executada.');
        scheduleFill(700);
        return true;
      }
      if (checkpoint === 'pagamento_incluindo' && !paymentAlreadyListed()) {
        // Se a janela de detalhe ainda não apareceu, aguarda. Depois de alguns
        // segundos libera uma nova tentativa segura, sem procurar controles globais.
        if (!checkpointElapsed(3500)) { scheduleFill(650); return true; }
        paymentNewOpened = false;
        setCheckpoint('pagamento_abrir');
        addDebugStep('Pagamento - nova tentativa', 'O detalhe não abriu em 3,5 s; liberando nova tentativa segura no Incluir da aba Pagamento.');
        scheduleFill(500);
        return true;
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

  function readDebugSteps() {
    try {
      const raw = localStorage.getItem(DEBUG_LOG_KEY) || '[]';
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr : [];
    } catch (_) { return []; }
  }

  function clearDebugSteps() {
    try { localStorage.removeItem(DEBUG_LOG_KEY); } catch (_) {}
  }

  function addDebugStep(msg, detail = '') {
    const m = String(msg || '').trim();
    const d = String(detail || '').trim();
    if (!m && !d) return;
    try {
      const now = Date.now();
      const arr = readDebugSteps();
      const last = arr[arr.length - 1];
      if (last && last.msg === m && last.detail === d) {
        last.t = now;
        last.count = Number(last.count || 1) + 1;
      } else {
        arr.push({ t: now, msg: m, detail: d, count: 1 });
      }
      while (arr.length > 120) arr.shift();
      localStorage.setItem(DEBUG_LOG_KEY, JSON.stringify(arr));
    } catch (_) {}
  }

  function debugElementName(el) {
    if (!el) return '(nenhum)';
    return String(el.id || el.name || el.tagName || '(sem id)');
  }

  function formatDebugLog() {
    const arr = readDebugSteps();
    const lines = [];
    const val = (id) => {
      try {
        const el = document.getElementById(id);
        if (!el) return '(não localizado)';
        const opt = el.tagName === 'SELECT' && el.selectedIndex >= 0 ? String(el.options[el.selectedIndex]?.textContent || '').trim() : '';
        return `${String(el.value || '-')} ${opt ? '- ' + opt : ''}`.trim();
      } catch (_) { return '(erro ao ler)'; }
    };
    lines.push('Organiza → NFA-e | Log de diagnóstico');
    lines.push(`Versão da extensão: ${chrome?.runtime?.getManifest?.().version || 'desconhecida'}`);
    lines.push(`Página: ${location.href}`);
    lines.push(`Foco atual: ${debugElementName(document.activeElement)}`);
    lines.push(`Checkpoint: ${checkpoint || '(vazio)'}`);
    lines.push(`Etapa NF-e: ${loadNfeStage() || '(vazia)'}`);
    lines.push(`Automação pausada: ${automationPaused ? 'sim' : 'não'}`);
    lines.push(`_f_tpNF: ${val('_f_tpNF')}`);
    lines.push(`_f_idDest: ${val('_f_idDest')}`);
    lines.push(`_f_cNatureza: ${val('_f_cNatureza')}`);
    lines.push(`_f_dest_enderDest_UF: ${val('_f_dest_enderDest_UF')}`);
    lines.push('');
    lines.push('Etapas:');
    if (!arr.length) lines.push('(sem etapas registradas)');
    for (const item of arr) {
      const dt = new Date(Number(item.t || Date.now()));
      const hh = String(dt.getHours()).padStart(2,'0');
      const mm = String(dt.getMinutes()).padStart(2,'0');
      const ss = String(dt.getSeconds()).padStart(2,'0');
      lines.push(`${hh}:${mm}:${ss} ${item.msg}${Number(item.count || 1) > 1 ? ' ×' + item.count : ''}`);
      if (item.detail) lines.push(`  ${item.detail}`);
    }
    return lines.join('\n');
  }

  async function copyDebugLog() {
    const text = formatDebugLog();
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) {}
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px;opacity:0';
      document.documentElement.appendChild(ta);
      ta.select();
      ta.setSelectionRange(0, ta.value.length);
      const ok = document.execCommand('copy');
      ta.remove();
      return !!ok;
    } catch (_) { return false; }
  }

  function renderDebugSteps(box) {
    if (!box) return;
    const host = box.querySelector('#organiza-nfae-log');
    if (!host) return;
    const arr = readDebugSteps().slice(-12);
    host.innerHTML = '';
    if (!arr.length) {
      const empty = document.createElement('div');
      empty.textContent = 'Aguardando etapas...';
      empty.style.color = '#777';
      host.appendChild(empty);
      return;
    }
    for (const item of arr) {
      const row = document.createElement('div');
      row.style.cssText = 'padding:3px 0;border-bottom:1px solid #eee;line-height:1.25';
      const dt = new Date(Number(item.t || Date.now()));
      const hh = String(dt.getHours()).padStart(2,'0');
      const mm = String(dt.getMinutes()).padStart(2,'0');
      const ss = String(dt.getSeconds()).padStart(2,'0');
      const head = document.createElement('div');
      head.style.cssText = 'font-weight:700;color:#1f2937';
      head.textContent = `${hh}:${mm}:${ss}  ${item.msg}${Number(item.count || 1) > 1 ? '  ×' + item.count : ''}`;
      row.appendChild(head);
      if (item.detail) {
        const det = document.createElement('div');
        det.style.cssText = 'color:#555;margin-top:1px';
        det.textContent = item.detail;
        row.appendChild(det);
      }
      host.appendChild(row);
    }
    host.scrollTop = host.scrollHeight;
  }

  function updateBanner(msg, detail = '') {
    addDebugStep(msg, detail);
    if (window.top !== window) return;
    let box = document.getElementById('organiza-nfae-box');
    if (!box) {
      box = document.createElement('div');
      box.id = 'organiza-nfae-box';
      box.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:2147483647;background:#fff;border:2px solid #2563eb;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.22);padding:10px 12px;max-width:430px;font:13px Arial;color:#111';
      box.innerHTML = '<div style="font-weight:700;margin-bottom:6px">Organiza → NFA-e</div><div id="organiza-nfae-msg" style="margin-bottom:4px"></div><div id="organiza-nfae-detail" style="font-size:11px;color:#555;margin-bottom:8px"></div><div style="font-size:11px;font-weight:700;margin:5px 0 3px">Etapas da automação</div><div id="organiza-nfae-log" style="font-size:10px;max-height:150px;overflow:auto;background:#f8fafc;border:1px solid #dbe3ef;border-radius:6px;padding:6px;margin-bottom:8px;min-width:390px"></div><button id="organiza-nfae-fill" style="margin-right:6px;padding:6px 9px;cursor:pointer">Iniciar automático</button><button id="organiza-nfae-pause" style="margin-right:6px;padding:6px 9px;cursor:pointer">Pausar</button><button id="organiza-nfae-copylog" style="margin-right:6px;padding:6px 9px;cursor:pointer">Copiar log</button><button id="organiza-nfae-clearlog" style="margin-right:6px;padding:6px 9px;cursor:pointer">Limpar log</button><button id="organiza-nfae-clear" style="padding:6px 9px;cursor:pointer">Limpar dados</button>';
      document.documentElement.appendChild(box);
      box.querySelector('#organiza-nfae-fill').addEventListener('click', async () => {
        workflowActive = true;
        automationPaused = false;
        checkpoint = '';
        resetNfeStage();
        nfeDestinoApplyPending = false;
        lastNfeSelectActionKey = '';
        lastNfeSelectActionAt = 0;
        clearDebugSteps();
        saveWorkflowState();
        updateBanner('Automação iniciada.', 'Etapas reiniciadas. Acompanhe o log abaixo para ver exatamente onde o fluxo parar.');
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
      box.querySelector('#organiza-nfae-copylog').addEventListener('click', async () => {
        const ok = await copyDebugLog();
        if (ok) updateBanner('Log copiado.', 'Cole aqui no ChatGPT. O texto inclui todas as etapas salvas e o estado atual dos campos da NF-e.');
        else updateBanner('Não consegui copiar o log.', 'Selecione o texto do log manualmente ou tente novamente.');
      });
      box.querySelector('#organiza-nfae-clearlog').addEventListener('click', () => {
        clearDebugSteps();
        addDebugStep('Log limpo.', 'Pronto para um novo teste.');
        renderDebugSteps(box);
      });
      box.querySelector('#organiza-nfae-clear').addEventListener('click', async () => {
        const answer = await runtimeMessage({type:'NFAE_CLEAR_PAYLOAD'});
        payload = null;
        if (answer && answer.ok) box.remove();
        else updateBanner('Não foi possível limpar os dados.', 'Atualize esta aba da SEFAZ e tente novamente.');
      });
      renderDebugSteps(box);
      if (!debugRenderTimer) {
        debugRenderTimer = setInterval(() => {
          const currentBox = document.getElementById('organiza-nfae-box');
          if (currentBox) renderDebugSteps(currentBox);
        }, 350);
      }
    }
    renderDebugSteps(box);
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
    // Somente top.frames[2] executa ações na SEFAZ. O frame superior existe apenas
    // para exibir o popup/log; frames auxiliares nunca preenchem nem navegam.
    if (!isAutomationControllerFrame()) return;
    loadWorkflowState();
    if (!workflowActive) return;
    if (checkpoint === 'concluido') return;
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

      // Proteção de sentido único: depois de Destinatário/Produto/Transporte/
      // Pagamento, uma tela antiga de NF-e não pode reiniciar Saída/Destino/UF.
      if (kind === 'nfe' && /^(destinatario_|produto_|transporte_|pagamento_|observacao_|concluido)/.test(String(checkpoint || ''))) {
        addDebugStep('Proteção anti-reinício', `Tela NF-e ignorada porque o fluxo já está em ${checkpoint}.`);
        scheduleFill(1200);
        return;
      }

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
        const jaPago = paymentAlreadyListed();
        updateBanner('Pagamento', jaPago ? 'Pagamento já listado; encerrando a automação.' : 'Abrindo Incluir automaticamente...');
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
    if (!isAutomationControllerFrame()) return;
    loadWorkflowState();
    if (!workflowActive || checkpoint === 'concluido') return;
    if (fillRetryTimer) clearTimeout(fillRetryTimer);
    fillRetryTimer = setTimeout(() => {
      fillRetryTimer = null;
      if (payload && !isPaused()) runFill(true);
    }, delay);
  }

  function acceptPayload(data) {
    if (!data) return;
    const fp = fingerprint(data);
    let sharedFp = '';
    try { sharedFp = sessionStorage.getItem(SESSION_PAYLOAD_FP) || ''; } catch (_) {}
    const trulyNew = !!sharedFp && fp !== sharedFp;
    const firstSharedLoad = !sharedFp;

    payload = data;
    payloadFingerprint = fp;

    // Apenas o frame controlador pode criar/reiniciar estado compartilhado.
    // O frame superior mantém o popup disponível, sem participar da automação.
    if (!isAutomationControllerFrame()) {
      if (isTopFrame()) {
        updateBanner('Dados do Organiza carregados.', 'Aguardando o frame principal da NFA-e executar a automação.');
      }
      return;
    }

    try { sessionStorage.setItem(SESSION_PAYLOAD_FP, fp); } catch (_) {}

    // Ao trocar de aba/frame a SVRS cria um novo contexto JavaScript. O mesmo
    // payload NÃO pode zerar o checkpoint; ele deve continuar do ponto anterior.
    if (!firstSharedLoad && !trulyNew) {
      loadWorkflowState();
      if (!automationPaused) { runFill(true); scheduleFill(900); }
      return;
    }

    firstScreenHelpClosed = false;
    resetNfeStage();
    productCfopRetries = 0;
    productIncludeOpened = false;
    productIncludeAttemptAt = 0;
    productListRefocusAt = 0;
    productListRefocusCount = 0;
    destinatarioActivated = false;
    productWorkflowDone = false;
    productTabActionAt = 0;
    paymentNewOpened = false;
    checkpointAt = 0;
    workflowActive = true;
    automationPaused = false;
    checkpoint = '';
    saveWorkflowState();
    {
      const uf = String(destinatarioDadosEfetivos(data).uf || '').trim().toUpperCase();
      const dest = String((data.operacao || {}).destino || (uf === 'RJ' ? 'Interna' : 'Interestadual'));
      const cfop = String((data.produto || {}).cfop || (dest === 'Interestadual' ? '6102' : '5102'));
      updateBanner('Dados do Organiza carregados.', `Primeira tela: cliente UF ${uf || '-'} / ${dest}. Confira o popup; após OK a extensão preencherá a NF-e automaticamente. CFOP esperado: ${cfop}.`);
    }
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
          // Somente o frame controlador altera estado do workflow. Antes todos os
          // frames recebiam START/RESUME e um deles podia reiniciar a primeira tela.
          if (!isAutomationControllerFrame()) return;
          if (msg.command === 'START') { workflowActive = true; automationPaused = false; checkpoint = ''; checkpointAt = 0; productCfopRetries = 0; productIncludeOpened = false; productIncludeAttemptAt = 0; productListRefocusAt = 0; productListRefocusCount = 0; paymentNewOpened = false; resetNfeStage(); }
          else if (msg.command === 'PAUSE') { workflowActive = true; automationPaused = true; checkpoint = msg.checkpoint || checkpoint || ''; }
          else if (msg.command === 'RESUME') {
            loadWorkflowState();
            workflowActive = true;
            automationPaused = false;
            checkpoint = msg.checkpoint || checkpoint || '';
            if (checkpoint && !checkpointAt) checkpointAt = Date.now();
            productCfopRetries = 0;
          }
          else if (msg.command === 'STOP') { workflowActive = false; automationPaused = false; checkpoint = 'concluido'; checkpointAt = Date.now(); }
          saveWorkflowState();
          if (msg.message) updateBanner(msg.message, msg.detail || '');
          if ((msg.command === 'START' || msg.command === 'RESUME') && payload) setTimeout(() => runFill(true), 120);
        }
      });
    } catch (_) {}
  }


  // Algumas abas do emissor antigo apenas alternam visibilidade e não mudam
  // a árvore DOM. Um clique real do usuário em uma aba deve disparar uma nova
  // leitura da tela, sem reagir aos cliques automáticos da própria extensão.
  document.addEventListener('click', (ev) => {
    if (!isAutomationControllerFrame()) return;
    if (!payload || !ev.isTrusted || isPaused()) return;
    const el = ev.target && (ev.target.closest ? ev.target.closest('a,button,input,[onclick],[role=tab]') : null);
    if (!el || (el.closest && (el.closest('#organiza-nfae-box') || el.closest('#organiza-nfae-first-help')))) return;
    scheduleFill(420);
  }, true);

  let mutationTimer = null;
  new MutationObserver((mutations) => {
    if (!isAutomationControllerFrame()) return;
    if (!payload || isPaused()) return;

    // Ignora alterações feitas pelo próprio painel da extensão. Antes, cada
    // atualização do texto do painel acionava o observer novamente e criava
    // um loop infinito de preenchimento/pisca-pisca.
    const relevant = mutations.some((m) => {
      const target = m && m.target;
      if (!target) return false;
      const el = target.nodeType === 1 ? target : target.parentElement;
      if (el && el.closest && (el.closest('#organiza-nfae-box') || el.closest('#organiza-nfae-first-help'))) return false;
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
    if (!isAutomationControllerFrame()) {
      clearInterval(payloadPollTimer);
      payloadPollTimer = null;
      return;
    }
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
