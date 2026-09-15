(function () {
  const STORAGE_KEY = 'organiza_nfae_payload';
  const SAVED_AT_KEY = 'organiza_nfae_saved_at';
  const MAX_AGE_MS = 4 * 60 * 60 * 1000;
  let payload = null;
  let filling = false;
  let lastRun = 0;
  let lastActionAt = 0;

  const norm = (s) => String(s || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase().replace(/\s+/g, ' ')
    .replace(/[\*:\-–—]+/g, ' ').trim();

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

    // Abas principais.
    if (txt.includes('tipo de documento') && txt.includes('razao social nome')) return 'destinatario';
    if (txt.includes('tipo de operacao') && txt.includes('natureza da operacao')) return 'nfe';
    if (txt.includes('informacoes adicionais de interesse do fisco')) return 'observacao';
    if (hash.includes('produtos') || (txt.includes('produtos e servicos') && txt.includes('linhas'))) return 'produtos_lista';
    if (hash.includes('pagamento') || (txt.includes('pagamentos') && txt.includes('linhas'))) return 'pagamento_lista';
    if (txt.includes('total nota fiscal') || txt.includes('base de calculo icms')) return 'total';
    if (txt.includes('transporte')) return 'transporte';
    if (txt.includes('referencias')) return 'referencias';
    if (txt.includes('cobranca')) return 'cobranca';
    return 'outro';
  }

  function fillNfe(d) {
    const op = d.operacao || {};
    const destUF = String((d.destinatario || {}).uf || '').trim().toUpperCase();
    const results = [];
    results.push(['Tipo de operação', setByLabel(['tipo de operação entrada saída', 'tipo de operacao entrada saida'], op.tipo || 'Saída', { tag: 'SELECT', reject: ['destino'] }).ok]);
    results.push(['Destino', setByLabel(['tipo de operação destino', 'tipo de operacao destino'], op.destino || (destUF === 'RJ' ? 'Interna' : 'Interestadual'), { tag: 'SELECT' }).ok]);
    results.push(['Natureza', setByLabel(['natureza da operação', 'natureza da operacao'], op.natureza || 'Venda de Mercadoria', { tag: 'SELECT' }).ok]);
    results.push(['Consumidor final', setByLabel(['consumidor final'], '1 - Sim', { tag: 'SELECT' }).ok]);
    results.push(['Finalidade', setByLabel(['finalidade de emissão', 'finalidade de emissao'], '1 - NF-e normal', { tag: 'SELECT' }).ok]);
    results.push(['Tipo atendimento', setByLabel(['tipo atendimento'], '2 - Operação NÃO Presencial, pela INTERNET', { tag: 'SELECT' }).ok]);
    results.push(['Intermediador', setByLabel(['ind intermediador marketplace', 'intermediador marketplace'], '0 - Operação sem intermediador', { tag: 'SELECT' }).ok]);
    // Data/hora da emissão e saída são responsabilidade da SEFAZ.
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
    const beforeTipo = tipoSelect ? norm(tipoSelect.options[tipoSelect.selectedIndex]?.textContent || tipoSelect.value) : '';
    const tipoOk = tipoSelect ? setSelect(tipoSelect, tipo) : false;
    results.push(['Tipo documento', tipoOk]);
    // Alguns formulários da SEFAZ reconstroem CPF/CNPJ ao trocar o tipo. A próxima rodada termina o preenchimento.
    if (tipoSelect && tipoOk && beforeTipo && !beforeTipo.includes(norm(tipo))) return results;

    const docLabels = tipo === 'CPF' ? ['cpf'] : ['cnpj'];
    results.push([tipo || 'Documento', setByLabel(docLabels, doc, { notTypes: ['checkbox', 'radio'] }).ok]);
    results.push(['Nome/Razão Social', setByLabel(['razão social nome', 'razao social nome'], x.nome, { notTypes: ['checkbox', 'radio'] }).ok]);

    const ie = String(x.inscricao_estadual || '').trim();
    const semIe = x.sem_inscricao_estadual === true || !ie || ie === '-';
    const semIeBox = findSemIeCheckbox();
    if (semIeBox) setValue(semIeBox, semIe);
    results.push(['Sem IE', !!semIeBox]);
    if (!semIe) {
      results.push(['Inscrição Estadual', setValue(findIeInput(), ie)]);
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
    results.push(['Grupo CFOP', setByLabel(['grupo cfop'], p.grupo_cfop || 'Venda de Mercadoria', { tag: 'SELECT' }).ok]);
    results.push(['CFOP', setByLabel(['cfop'], p.cfop, { tag: 'SELECT', reject: ['grupo'] }).ok]);
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
    return [['PIS CST', setByLabel(['tributação pis cst', 'tributacao pis cst'], p.pis_cst || '07', { tag: 'SELECT' }).ok]];
  }

  function fillProdutoCofins(d) {
    const p = d.produto || {};
    return [['COFINS CST', setByLabel(['tributação cofins cst', 'tributacao cofins cst'], p.cofins_cst || '06', { tag: 'SELECT' }).ok]];
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
    if (kind === 'produtos_lista' && txt.includes('nao ha item para ser listado')) {
      if (clickByText(['incluir'], true)) { lastActionAt = now; return true; }
    }
    if (kind === 'pagamento_lista' && txt.includes('nao ha item para ser listado')) {
      if (clickByText(['incluir'], true)) { lastActionAt = now; return true; }
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
      box.innerHTML = '<div style="font-weight:700;margin-bottom:6px">Organiza → NFA-e</div><div id="organiza-nfae-msg" style="margin-bottom:4px"></div><div id="organiza-nfae-detail" style="font-size:11px;color:#555;margin-bottom:8px"></div><button id="organiza-nfae-fill" style="margin-right:6px;padding:6px 9px;cursor:pointer">Preencher agora</button><button id="organiza-nfae-clear" style="padding:6px 9px;cursor:pointer">Limpar dados</button>';
      document.documentElement.appendChild(box);
      box.querySelector('#organiza-nfae-fill').addEventListener('click', () => runFill(true));
      box.querySelector('#organiza-nfae-clear').addEventListener('click', async () => {
        await chrome.storage.local.remove([STORAGE_KEY, SAVED_AT_KEY]);
        payload = null;
        box.remove();
      });
    }
    const m = box.querySelector('#organiza-nfae-msg'); if (m) m.textContent = msg;
    const det = box.querySelector('#organiza-nfae-detail'); if (det) det.textContent = detail || '';
  }

  function noTouchMessage(kind) {
    const messages = {
      emitente: 'Emitente é a Karaokê RJ e não será alterado pelo Organiza.',
      total: 'Aba Total: cálculo deixado para a SEFAZ.',
      transporte: 'Aba Transporte: permanece vazia.',
      referencias: 'Aba Referências: permanece vazia.',
      cobranca: 'Aba Cobrança: permanece vazia.'
    };
    return messages[kind] || 'Dados do Organiza carregados. Abra uma aba da NFA-e para preencher.';
  }

  function runFill(force = false) {
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
      else if (['emitente', 'total', 'transporte', 'referencias', 'cobranca'].includes(kind)) {
        updateBanner(noTouchMessage(kind), 'Nenhum campo desta tela será alterado.');
        return;
      } else if (kind === 'produtos_lista') {
        updateBanner('Produtos e Serviços', bodyText().includes('nao ha item para ser listado') ? 'Clique em Incluir (ou aguarde a abertura automática).' : 'Produto já listado; não será duplicado.');
        return;
      } else if (kind === 'pagamento_lista') {
        updateBanner('Pagamento', bodyText().includes('nao ha item para ser listado') ? 'Clique em Incluir (ou aguarde a abertura automática).' : 'Pagamento já listado; não será duplicado.');
        return;
      } else {
        updateBanner(noTouchMessage(kind));
        return;
      }

      const sum = summarize(kind, res);
      if (sum) {
        const detail = sum.missing.length ? 'Não localizados nesta tela: ' + sum.missing.join(', ') : 'Todos os campos previstos desta tela foram localizados.';
        updateBanner(`Preenchimento: ${sum.ok}/${sum.total} campos`, detail);
      }
    } catch (e) {
      updateBanner('Dados carregados, mas houve falha em alguns campos.', String(e && e.message || e));
    } finally {
      filling = false;
    }
  }

  async function loadPayload() {
    const obj = await chrome.storage.local.get([STORAGE_KEY, SAVED_AT_KEY]);
    const saved = obj[SAVED_AT_KEY] || 0;
    if (!obj[STORAGE_KEY] || Date.now() - saved > MAX_AGE_MS) {
      payload = null;
      return;
    }
    payload = obj[STORAGE_KEY];
    updateBanner('Dados do Organiza carregados.');
    runFill(true);
    let tries = 0;
    const timer = setInterval(() => {
      if (!payload || tries++ > 25) return clearInterval(timer);
      runFill(true);
    }, 800);
  }

  chrome.runtime.onMessage.addListener((msg) => { if (msg && msg.type === 'NFAE_PREENCHER') loadPayload(); });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && (changes[STORAGE_KEY] || changes.organiza_nfae_trigger)) loadPayload();
  });
  new MutationObserver(() => { if (payload) runFill(false); }).observe(document.documentElement, { subtree: true, childList: true });
  loadPayload();
})();
