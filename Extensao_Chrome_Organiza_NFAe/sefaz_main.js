(function () {
  const ACTIVATE_EVENT = 'organiza-nfae-main-activate';
  const CHANGE_EVENT = 'organiza-nfae-main-change';
  const SELECT_VALUE_EVENT = 'organiza-nfae-main-select-value';
  const FRETE_SEM_EVENT = 'organiza-nfae-main-frete-sem';
  const PRODUCT_SEQUENCE_EVENT = 'organiza-nfae-main-product-sequence';
  const OPEN_PAYMENT_EVENT = 'organiza-nfae-main-open-payment';
  const TRANSPORT_SEM_EVENT = 'organiza-nfae-main-transport-sem';

  function runLegacyScript(el) {
    if (!el) return false;
    const href = String(el.getAttribute && el.getAttribute('href') || '').trim();
    const onclick = String(el.getAttribute && el.getAttribute('onclick') || '').trim();
    const hrefJs = /^javascript\s*:/i.test(href) ? href.replace(/^javascript\s*:/i, '') : '';
    const script = [hrefJs, onclick].filter(Boolean).join(';');

    // As abas principais desta versão da NFA-e são âncoras da própria página
    // (avulsaNFe_edicao.aspx?novo=1#destinatario, #produtos, #transporte...).
    // Não depender de um clique sintético: navegar diretamente para o hash e
    // deixar o próprio portal tratar a mudança de aba.
    if (href && !hrefJs && href.includes('#')) {
      try {
        const url = new URL(href, window.location.href);
        const current = new URL(window.location.href);
        if (url.origin === current.origin && url.pathname === current.pathname && url.search === current.search && url.hash) {
          const oldHref = window.location.href;
          // Primeiro dispara o click para manter qualquer handler legado existente.
          try { el.click(); } catch (_) {}
          // Se o handler bloqueou a navegação, força o hash diretamente.
          if (window.location.hash !== url.hash) {
            window.location.hash = url.hash;
          } else {
            // Se o hash já era o mesmo mas o painel não sincronizou, reavisa a página.
            try {
              window.dispatchEvent(new HashChangeEvent('hashchange', { oldURL: oldHref, newURL: url.href }));
            } catch (_) {
              try { window.dispatchEvent(new Event('hashchange')); } catch (_) {}
            }
          }
          try { console.debug('[Organiza NFA-e] hash principal acionado:', url.hash); } catch (_) {}
          return true;
        }
      } catch (_) {}
    }

    // Preferir chamada direta do __doPostBack. Isso evita depender de eval/Function
    // em páginas antigas da SVRS e reproduz de forma mais fiel o clique das abas.
    const pb = script.match(/__doPostBack\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)/i);
    if (pb && typeof window.__doPostBack === 'function') {
      try {
        window.__doPostBack(pb[1], pb[2] || '');
        return true;
      } catch (_) {}
    }

    // Alguns controles gerados pelo WebForms usam WebForm_PostBackOptions.
    const wpo = script.match(/WebForm_PostBackOptions\(\s*['\"]([^'\"]+)['\"]/i);
    if (wpo && typeof window.__doPostBack === 'function') {
      try {
        window.__doPostBack(wpo[1], '');
        return true;
      } catch (_) {}
    }

    // Se não for possível extrair o postback, deixa o próprio navegador executar
    // o onclick/href do elemento, exatamente como no clique do usuário.
    try {
      el.click();
      return true;
    } catch (_) {}
    return false;
  }


  function mainFrameDoc() {
    try {
      if (window.top && window.top.frames && window.top.frames.length > 2 && window.top.frames[2].document) {
        return window.top.frames[2].document;
      }
    } catch (_) {}
    return document;
  }

  function findTabInDoc(doc, hashName) {
    const wanted = '#' + String(hashName || '').replace(/^#/, '').toLowerCase();
    try {
      return Array.from(doc.querySelectorAll('a')).find((a) => {
        const href = String(a.getAttribute('href') || '').toLowerCase();
        return href.includes(wanted);
      }) || null;
    } catch (_) { return null; }
  }

  function clickReal(el) {
    if (!el) return false;
    try { el.focus(); } catch (_) {}
    try { el.click(); return true; } catch (_) { return false; }
  }

  function humanTabClick(el) {
    if (!el) return false;
    try { window.focus(); } catch (_) {}
    try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_) {}
    let x = 0, y = 0;
    try {
      const r = el.getBoundingClientRect();
      x = r.left + r.width / 2;
      y = r.top + r.height / 2;
    } catch (_) {}
    const types = ['pointerover','mouseover','pointerdown','mousedown','pointerup','mouseup','click'];
    let sent = false;
    for (const type of types) {
      try {
        const Ctor = type.startsWith('pointer') && typeof window.PointerEvent === 'function'
          ? window.PointerEvent : window.MouseEvent;
        el.dispatchEvent(new Ctor(type, {
          bubbles: true,
          cancelable: true,
          view: window,
          clientX: x,
          clientY: y,
          button: 0,
          buttons: type.includes('down') ? 1 : 0
        }));
        sent = true;
      } catch (_) {}
    }
    return sent;
  }

  function findTabSpanByText(doc, wantedText) {
    const wanted = String(wantedText || '').trim().toLowerCase();
    try {
      return Array.from(doc.querySelectorAll('span')).find((sp) =>
        String(sp.textContent || '').trim().toLowerCase() === wanted
      ) || null;
    } catch (_) { return null; }
  }

  document.addEventListener(PRODUCT_SEQUENCE_EVENT, function () {
    // Listener real da SEFAZ descoberto no DevTools chama FpcTabs.GoTo(hash).
    // Usamos a própria API interna; clique no link fica apenas como fallback.
    const doc = mainFrameDoc();
    const targetWin = doc && doc.defaultView;
    let ativou = false;
    try {
      if (targetWin && targetWin.FpcTabs && typeof targetWin.FpcTabs.GoTo === 'function') {
        targetWin.FpcTabs.GoTo('produtos');
        ativou = true;
      }
    } catch (_) {}
    if (!ativou) {
      const produtosSpan = findTabSpanByText(doc, 'Produtos e Serviços');
      const produtosLink = (produtosSpan && produtosSpan.closest('a')) || findTabInDoc(doc, 'produtos');
      if (produtosLink) clickReal(produtosLink);
    }
    try { console.debug('[Organiza NFA-e] MAIN Produto: FpcTabs.GoTo(produtos) -> btIncDet'); } catch (_) {}

    setTimeout(function () {
      const doc2 = mainFrameDoc();
      const incluir = doc2.getElementById('btIncDet') ||
        Array.from(doc2.querySelectorAll('input[type="button"],button')).find((el) =>
          String(el.id || '') === 'btIncDet' || String(el.name || '') === 'btIncDet'
        );
      if (incluir) {
        clickReal(incluir);
        try { console.debug('[Organiza NFA-e] MAIN btIncDet clicado após focus()+click() no <a> da aba Produtos.'); } catch (_) {}
      } else {
        try { console.debug('[Organiza NFA-e] MAIN btIncDet não localizado após ativar Produtos.'); } catch (_) {}
      }
    }, 1000);
  }, true);

  document.addEventListener(OPEN_PAYMENT_EVENT, function () {
    // 1.0.65: abrir Pagamento pelo LINK REAL, igual ao clique manual confirmado.
    // Não usar FpcTabs.GoTo aqui: em alguns estados da SVRS ele pode navegar no
    // contexto errado e carregar outra NFA-e dentro do frame atual.
    const doc = mainFrameDoc();
    const pagamentoSpan = findTabSpanByText(doc, 'Pagamento');
    const pagamentoLink = (pagamentoSpan && pagamentoSpan.closest('a')) || findTabInDoc(doc, 'pagamento');
    if (pagamentoLink) clickReal(pagamentoLink);
    try { console.debug('[Organiza NFA-e] MAIN Pagamento: link real focus()+click() acionado.'); } catch (_) {}
  }, true);

  document.addEventListener(TRANSPORT_SEM_EVENT, function () {
    // Localiza o select diretamente no frame confirmado e reproduz literalmente
    // o teste de Console: focus(); value='9'; change.
    const doc = mainFrameDoc();
    let frete = null;
    try {
      frete = doc.getElementById('f_transp_modFrete') ||
        Array.from(doc.querySelectorAll('select')).find((el) => String(el.id || '') === 'f_transp_modFrete');
    } catch (_) {}
    if (!frete) return;
    try { frete.focus(); } catch (_) {}
    try {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
      if (setter) setter.call(frete, '9');
      else frete.value = '9';
      const opts = Array.from(frete.options || []);
      const idx = opts.findIndex((o) => String(o.value || '') === '9');
      if (idx >= 0) frete.selectedIndex = idx;
      frete.dispatchEvent(new Event('change', { bubbles: true }));
      try { console.debug('[Organiza NFA-e] MAIN Transporte Sem frete aplicado:', frete.value); } catch (_) {}
    } catch (_) {}
  }, true);

  document.addEventListener(ACTIVATE_EVENT, function (ev) {
    const el = ev && ev.target;
    if (!el || !el.tagName) return;
    try { el.focus({ preventScroll: true }); } catch (_) { try { el.focus(); } catch (_) {} }
    runLegacyScript(el);
  }, true);

  document.addEventListener(SELECT_VALUE_EVENT, function (ev) {
    const el = ev && ev.target;
    if (!el || el.tagName !== 'SELECT') return;
    const raw = String(el.getAttribute('data-organiza-select-target') || '');
    try {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
      if (setter) setter.call(el, raw);
      else el.value = raw;
      const opts = Array.from(el.options || []);
      const idx = opts.findIndex((o) => String(o.value || '') === raw);
      if (idx >= 0) el.selectedIndex = idx;
      // Uma seleção feita pelo usuário em <select> gera input + change. Dispara
      // exatamente essa dupla no MAIN world para os observers legados da SVRS.
      try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) {}
      try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch (_) {}
    } finally {
      try { el.removeAttribute('data-organiza-select-target'); } catch (_) {}
    }
  }, true);



  document.addEventListener(FRETE_SEM_EVENT, function (ev) {
    const el = ev && ev.target;
    if (!el || el.tagName !== 'SELECT' || String(el.id || '') !== 'f_transp_modFrete') return;
    try { el.focus(); } catch (_) {}
    try {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
      if (setter) setter.call(el, '9');
      else el.value = '9';
      const opts = Array.from(el.options || []);
      const idx = opts.findIndex((o) => String(o.value || '') === '9');
      if (idx >= 0) el.selectedIndex = idx;
      // Reproduz exatamente o teste validado no Console: focus -> value 9 -> change.
      el.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (_) {}
  }, true);

  document.addEventListener(CHANGE_EVENT, function (ev) {
    const el = ev && ev.target;
    if (!el || !el.tagName) return;
    // Não roubar o foco e não emitir uma sequência input/change/blur em SELECT.
    // Para os combos WebForms da NFA-e, um único change reproduz a ação manual
    // e evita postbacks duplicados/loop de atualização.
    if (el.tagName === 'SELECT') {
      try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch (_) {}
    } else {
      try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) {}
      try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch (_) {}
    }
  }, true);
})();
