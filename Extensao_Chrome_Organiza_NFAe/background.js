const SEFAZ_MATCH = 'https://svp-extranet.svrs.rs.gov.br/*';
const SEFAZ_HOME = 'https://nfae.fazenda.rj.gov.br/sefaz-dfe-nfae/paginas/inicio.faces';
const STORAGE_KEY = 'organiza_nfae_payload';
const SAVED_AT_KEY = 'organiza_nfae_saved_at';
const MAX_AGE_MS = 4 * 60 * 60 * 1000;

async function savePayload(data) {
  await chrome.storage.local.set({[STORAGE_KEY]: data, [SAVED_AT_KEY]: Date.now()});
}

async function getPayload() {
  const obj = await chrome.storage.local.get([STORAGE_KEY, SAVED_AT_KEY]);
  const saved = Number(obj[SAVED_AT_KEY] || 0);
  if (!obj[STORAGE_KEY] || !saved || Date.now() - saved > MAX_AGE_MS) return null;
  return obj[STORAGE_KEY];
}

async function sendPayloadToTab(tabId, payload) {
  if (!tabId || !payload) return false;
  try {
    // A NFA-e da SVRS usa frames internos. Envia o payload para cada frame que
    // tiver o content script, não apenas para o documento principal.
    let frames = [];
    try { frames = await chrome.webNavigation.getAllFrames({tabId}) || []; } catch (_) {}
    if (!frames.length) frames = [{frameId: 0}];

    let delivered = 0;
    for (const frame of frames) {
      try {
        await chrome.tabs.sendMessage(tabId, {type: 'NFAE_PAYLOAD', payload}, {frameId: frame.frameId});
        delivered++;
      } catch (_) {}
    }
    return delivered > 0;
  } catch (_) {
    return false;
  }
}


async function broadcastWorkflowToTab(tabId, command, extra = {}) {
  if (!tabId) return false;
  let frames = [];
  try { frames = await chrome.webNavigation.getAllFrames({tabId}) || []; } catch (_) {}
  if (!frames.length) frames = [{frameId: 0}];
  let delivered = 0;
  for (const frame of frames) {
    try {
      await chrome.tabs.sendMessage(tabId, Object.assign({type:'NFAE_WORKFLOW_COMMAND', command}, extra), {frameId: frame.frameId});
      delivered++;
    } catch (_) {}
  }
  return delivered > 0;
}

async function waitTabComplete(tabId, timeoutMs = 15000) {
  const current = await chrome.tabs.get(tabId).catch(() => null);
  if (current && current.status === 'complete') return true;
  return await new Promise((resolve) => {
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      resolve(ok);
    };
    const onUpdated = (id, info) => {
      if (id === tabId && info.status === 'complete') finish(true);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    chrome.tabs.onUpdated.addListener(onUpdated);
  });
}

async function focusSefaz(payload) {
  const tabs = await chrome.tabs.query({url: SEFAZ_MATCH});
  if (tabs && tabs.length) {
    const tab = tabs.find(t => t.active) || tabs[0];
    if (tab.windowId) await chrome.windows.update(tab.windowId, {focused: true});
    if (tab.id) {
      await chrome.tabs.update(tab.id, {active: true});
      let ok = await sendPayloadToTab(tab.id, payload);
      if (!ok) {
        // A aba pode ter ficado com um content-script da versão anterior.
        await chrome.tabs.reload(tab.id);
        await waitTabComplete(tab.id);
        ok = await sendPayloadToTab(tab.id, payload);
      }
      return {found: true, tabId: tab.id, delivered: ok};
    }
  }
  const tab = await chrome.tabs.create({url: SEFAZ_HOME, active: true});
  return {found: false, tabId: tab.id, delivered: false};
}



const NFSE_MATCH = 'https://www.nfse.gov.br/EmissorNacional/*';
const NFSE_HOME = 'https://www.nfse.gov.br/EmissorNacional/DPS/Pessoas';
const NFSE_STORAGE_KEY = 'organiza_nfse_payload';
const NFSE_SAVED_AT_KEY = 'organiza_nfse_saved_at';

async function saveNfsePayload(data) {
  await chrome.storage.local.set({[NFSE_STORAGE_KEY]: data, [NFSE_SAVED_AT_KEY]: Date.now()});
}
async function getNfsePayload() {
  const obj = await chrome.storage.local.get([NFSE_STORAGE_KEY, NFSE_SAVED_AT_KEY]);
  const saved = Number(obj[NFSE_SAVED_AT_KEY] || 0);
  if (!obj[NFSE_STORAGE_KEY] || !saved || Date.now() - saved > MAX_AGE_MS) return null;
  return obj[NFSE_STORAGE_KEY];
}
async function sendNfsePayloadToTab(tabId, payload) {
  if (!tabId || !payload) return false;
  try {
    await chrome.tabs.sendMessage(tabId, {type:'NFSE_PAYLOAD', payload});
    return true;
  } catch (_) { return false; }
}
async function focusNfse(payload) {
  const tabs = await chrome.tabs.query({url:['https://www.nfse.gov.br/EmissorNacional/*','https://nfse.gov.br/EmissorNacional/*']});
  if (tabs && tabs.length) {
    const tab = tabs.find(t => t.active) || tabs[0];
    if (tab.windowId) await chrome.windows.update(tab.windowId,{focused:true});
    if (tab.id) {
      const currentUrl = String(tab.url || '');
      if (!currentUrl.includes('/EmissorNacional/DPS/Pessoas')) {
        await chrome.tabs.update(tab.id,{active:true,url:NFSE_HOME});
        await waitTabComplete(tab.id);
      } else {
        await chrome.tabs.update(tab.id,{active:true});
      }
      let ok = await sendNfsePayloadToTab(tab.id,payload);
      if (!ok) { await chrome.tabs.reload(tab.id); await waitTabComplete(tab.id); ok = await sendNfsePayloadToTab(tab.id,payload); }
      return {found:true,tabId:tab.id,delivered:ok};
    }
  }
  const tab = await chrome.tabs.create({url:NFSE_HOME,active:true});
  await waitTabComplete(tab.id);
  const ok = await sendNfsePayloadToTab(tab.id,payload);
  return {found:false,tabId:tab.id,delivered:ok};
}

async function executeMainAction(tabId, action) {
  if (!tabId || !action) return {ok:false, error:'ação inválida'};
  try {
    // 1.0.60: ações críticas executadas no MAIN world do frame superior.
    // Produto reproduz literalmente o comando do Console que funcionou e devolve
    // diagnóstico detalhado ao popup para o botão Copiar log.
    const results = await chrome.scripting.executeScript({
      target: {tabId, frameIds: [0]},
      world: 'MAIN',
      func: async (actionName) => {
        const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

        let win = null;
        let doc = null;
        try {
          win = window.top.frames[2];
          doc = win && win.document;
        } catch (_) {}
        if (!win || !doc) return {ok:false, error:'top.frames[2] não localizado'};

        const findTab = (name) => Array.from(doc.querySelectorAll('a')).find((a) =>
          String(a.getAttribute('href') || '').toLowerCase().includes('#' + name)
        ) || null;
        const findSpan = (text) => Array.from(doc.querySelectorAll('span')).find((sp) =>
          String(sp.textContent || '').trim().toLowerCase() === String(text || '').trim().toLowerCase()
        ) || null;
        const clickReal = (el) => {
          if (!el) return false;
          try { win.focus(); } catch (_) {}
          try { el.focus(); } catch (_) {}
          try { el.click(); return true; } catch (_) { return false; }
        };

        if (actionName === 'PRODUCT_INCLUDE_SEQUENCE') {
          // 1.0.60: reproduz literalmente o comando confirmado no Console:
          // localizar <span>Produtos e Serviços</span> -> closest('a') -> focus() -> click()
          // -> aguardar a SEFAZ -> localizar #btIncDet -> focus() -> click().
          // Retorna cada estágio ao popup para o Copiar log mostrar onde falhou.
          const fpcTabsFound = !!(win.FpcTabs && typeof win.FpcTabs.GoTo === 'function');
          const span = findSpan('Produtos e Serviços');
          const aba = (span && span.closest('a')) || findTab('produtos');
          if (!aba) {
            return {
              ok:false, action:actionName, frameFound:true, tabFound:false,
              fpcTabsFound, activationMode:'link.focus()+click()',
              found:false, clicked:false, formOpened:false,
              error:'link da aba Produtos e Serviços não localizado'
            };
          }

          let tabActivated = false;
          let activationError = '';
          try {
            try { win.focus(); } catch (_) {}
            try { aba.focus(); } catch (_) {}
            aba.click();
            tabActivated = true;
          } catch (e) {
            activationError = String(e && (e.message || e));
          }

          // O teste manual funcionou imediatamente com ~1 s entre a aba e o Incluir.
          await sleep(1000);

          let incluir = null;
          let attempts = 0;
          for (let i = 0; i < 8; i++) {
            attempts = i + 1;
            incluir = doc.getElementById('btIncDet') ||
              Array.from(doc.querySelectorAll('input[type="button"],button')).find((el) =>
                String(el.id || '') === 'btIncDet' || String(el.name || '') === 'btIncDet'
              ) || null;
            if (incluir) break;
            await sleep(150);
          }

          if (!incluir) {
            return {
              ok:false, action:actionName, frameFound:true, tabFound:true,
              tabActivated, activationMode:'link.focus()+click()', fpcTabsFound,
              href:String(aba.getAttribute('href') || ''), attempts,
              found:false, clicked:false, formOpened:false,
              error:activationError || 'btIncDet não localizado após ativar Produtos'
            };
          }

          let clicked = false;
          let clickError = '';
          try {
            try { incluir.focus(); } catch (_) {}
            incluir.click();
            clicked = true;
          } catch (e) {
            clickError = String(e && (e.message || e));
          }

          // Dá tempo para a tela do item abrir e confirma pela presença de
          // Validar Item/Salvar Item ou pelo aumento relevante de controles.
          await sleep(450);
          let formOpened = false;
          try {
            const bodyText = String(doc.body && (doc.body.innerText || doc.body.textContent) || '').toLowerCase();
            const hasItemActions = bodyText.includes('validar item') || bodyText.includes('salvar item');
            const visibleControls = Array.from(doc.querySelectorAll('input,select,textarea,button')).filter((el) => {
              try { return !el.disabled && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length); } catch (_) { return false; }
            }).length;
            formOpened = hasItemActions || visibleControls >= 12;
          } catch (_) {}

          return {
            ok:clicked,
            action:actionName,
            frameFound:true,
            tabFound:true,
            tabActivated,
            activationMode:'link.focus()+click()',
            fpcTabsFound,
            href:String(aba.getAttribute('href') || ''),
            attempts,
            found:true,
            clicked,
            formOpened,
            error:activationError || clickError || ''
          };
        }

        if (actionName === 'TRANSPORT_SEM_FRETE') {
          const frete = doc.getElementById('f_transp_modFrete') ||
            Array.from(doc.querySelectorAll('select')).find((el) => String(el.id || '') === 'f_transp_modFrete') || null;
          if (!frete) return {ok:false, action:actionName, found:false};
          try { win.focus(); } catch (_) {}
          try { frete.focus(); } catch (_) {}
          try {
            frete.value = '9';
            const opts = Array.from(frete.options || []);
            const idx = opts.findIndex((o) => String(o.value || '') === '9');
            if (idx >= 0) frete.selectedIndex = idx;
            frete.dispatchEvent(new win.Event('change', {bubbles:true}));
          } catch (_) {}
          return {ok:String(frete.value || '') === '9', action:actionName, value:String(frete.value || '')};
        }

        if (actionName === 'PAYMENT_INCLUDE') {
          // 1.0.65: só depois de a aba Pagamento ter sido aberta pelo link real.
          // Reobtém top.frames[2].document após a troca de aba e procura SOMENTE
          // o controle Incluir, nunca 'Novo' e nunca fora desse documento.
          let pwin = null;
          let pdoc = null;
          try {
            pwin = window.top.frames[2];
            pdoc = pwin && pwin.document;
          } catch (_) {}
          if (!pwin || !pdoc) return {ok:false, action:actionName, frameFound:false, error:'frame principal não localizado após abrir Pagamento'};

          const normText = (v) => String(v || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim().toLowerCase().replace(/\s+/g, ' ');
          const visible = (el) => {
            if (!el) return false;
            try {
              const st = pwin.getComputedStyle(el);
              if (st && (st.display === 'none' || st.visibility === 'hidden')) return false;
              return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
            } catch (_) { return true; }
          };
          const labelOf = (el) => {
            let img = null;
            try { img = el.querySelector && el.querySelector('img'); } catch (_) {}
            return normText([
              el.textContent || '', el.value || '', el.title || '', el.alt || '',
              el.getAttribute && (el.getAttribute('aria-label') || ''),
              img ? (img.alt || '') : '', img ? (img.title || '') : ''
            ].join(' '));
          };

          const candidates = Array.from(pdoc.querySelectorAll('a,button,input[type="button"],input[type="submit"],[onclick],[role="button"]')).filter(visible);
          const sample = candidates.slice(0, 30).map((el) => ({tag:el.tagName,id:String(el.id||''),name:String(el.name||''),label:labelOf(el),href:String(el.getAttribute&&el.getAttribute('href')||'')}));
          let incluir = candidates.find((el) => labelOf(el) === 'incluir') || null;
          if (!incluir) incluir = candidates.find((el) => labelOf(el).startsWith('incluir ')) || null;
          if (!incluir) {
            const body = normText(pdoc.body && (pdoc.body.innerText || pdoc.body.textContent) || '');
            return {ok:false, action:actionName, frameFound:true, found:false, clicked:false, error:'controle Incluir da aba Pagamento não localizado', bodyHasPagamentos:body.includes('pagamentos'), candidates:sample};
          }

          let clicked = false;
          let error = '';
          try {
            try { pwin.focus(); } catch (_) {}
            try { incluir.focus(); } catch (_) {}
            incluir.click();
            clicked = true;
          } catch (e) { error = String(e && (e.message || e)); }
          return {
            ok:clicked, action:actionName, frameFound:true, found:true, clicked,
            activationMode:'Pagamento/Incluir focus()+click() local',
            controlId:String(incluir.id || ''), controlName:String(incluir.name || ''),
            controlLabel:labelOf(incluir), error
          };
        }

        if (actionName === 'VALIDATE_NOTE') {
          // 1.0.63: após o Pagamento, clicar somente no botão GLOBAL "Validar".
          // Nunca clicar em "Validar Item", Salvar ou Emitir.
          const normText = (v) => String(v || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim().toLowerCase().replace(/\s+/g, ' ');
          const visible = (el) => {
            if (!el) return false;
            try {
              const st = win.getComputedStyle(el);
              if (st && (st.display === 'none' || st.visibility === 'hidden')) return false;
              return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
            } catch (_) { return true; }
          };
          const labelsOf = (el) => {
            let nested = null;
            try { nested = el.querySelector && el.querySelector('img'); } catch (_) {}
            return [
              el.textContent || '', el.value || '', el.title || '', el.alt || '',
              el.getAttribute && (el.getAttribute('aria-label') || ''),
              nested ? (nested.alt || '') : '', nested ? (nested.title || '') : ''
            ].map(normText).filter(Boolean);
          };
          const labelOf = (el) => labelsOf(el).join(' | ');

          let validar = null;
          const direct = Array.from(doc.querySelectorAll('button,a,input[type="button"],input[type="submit"],input[type="image"],[onclick]'));
          validar = direct.find((el) => {
            if (!visible(el)) return false;
            const labs = labelsOf(el);
            return labs.includes('validar') && !labs.includes('validar item');
          }) || null;

          if (!validar) {
            const labels = Array.from(doc.querySelectorAll('span,td,div')).filter(visible);
            for (const lab of labels) {
              if (normText(lab.textContent || '') !== 'validar') continue;
              const candidate = lab.closest && lab.closest('button,a,input,[onclick]');
              if (candidate && visible(candidate) && !labelsOf(candidate).includes('validar item')) { validar = candidate; break; }
            }
          }

          if (!validar) {
            return {ok:false, action:actionName, frameFound:true, found:false, clicked:false, error:'botão global Validar não localizado'};
          }

          let clicked = false;
          let error = '';
          try {
            try { win.focus(); } catch (_) {}
            try { validar.focus(); } catch (_) {}
            validar.click();
            clicked = true;
          } catch (e) {
            error = String(e && (e.message || e));
          }
          return {
            ok:clicked, action:actionName, frameFound:true, found:true, clicked,
            activationMode:'global Validar focus()+click()',
            error,
            controlId:String(validar.id || ''),
            controlName:String(validar.name || ''),
            controlLabel:labelOf(validar)
          };
        }

        if (actionName === 'OPEN_PAYMENT') {
          // 1.0.65: NÃO usar FpcTabs.GoTo('pagamento') aqui. Na SVRS isso pode
          // executar no contexto errado e carregar outra NFA-e dentro do frame atual.
          // Reproduz literalmente o teste manual/Console que funcionou: localizar
          // o link real <a href="#pagamento">, focus() e click().
          let aba = null;
          const span = findSpan('Pagamento');
          aba = (span && span.closest('a')) || findTab('pagamento');
          if (!aba) {
            return {ok:false, action:actionName, tabFound:false, tabActivated:false, activationMode:'link.focus()+click()', error:'link real da aba Pagamento não localizado'};
          }
          let ok = false;
          let error = '';
          try {
            try { win.focus(); } catch (_) {}
            try { aba.focus(); } catch (_) {}
            aba.click();
            ok = true;
          } catch (e) { error = String(e && (e.message || e)); }

          // Aguarda o painel Pagamento realmente trocar antes de autorizar o Incluir.
          await sleep(700);
          let ready = false;
          let includeFound = false;
          let body = '';
          try {
            const currentDoc = window.top.frames[2] && window.top.frames[2].document;
            body = String(currentDoc && (currentDoc.body.innerText || currentDoc.body.textContent) || '').toLowerCase();
            const controls = currentDoc ? Array.from(currentDoc.querySelectorAll('a,button,input[type="button"],input[type="submit"],[onclick],[role="button"]')) : [];
            const norm = (v) => String(v || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').trim().toLowerCase().replace(/\s+/g,' ');
            includeFound = controls.some((el) => {
              const img = el.querySelector && el.querySelector('img');
              const lab = norm([el.textContent||'',el.value||'',el.title||'',img?(img.alt||img.title||''):''].join(' '));
              return lab === 'incluir' || lab.startsWith('incluir ');
            });
            ready = body.includes('pagamentos') || includeFound;
          } catch (_) {}
          return {
            ok, action:actionName, tabFound:true, tabActivated:ok,
            activationMode:'link.focus()+click()', href:String(aba.getAttribute('href') || ''),
            paymentReady:ready, includeFound, error
          };
        }

        return {ok:false, error:'ação desconhecida'};
      },
      args: [action]
    });
    const useful = (results || []).map((r) => r && r.result).find(Boolean) || null;
    return useful || {ok:false, error:'ação MAIN sem retorno'};
  } catch (err) {
    return {ok:false, error:String(err && (err.message || err))};
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return;

  if (msg.type === 'NFSE_PREPARADO') {
    (async () => {
      if (!msg.payload) throw new Error('Payload da NFS-e não informado.');
      await saveNfsePayload(msg.payload);
      return await focusNfse(msg.payload);
    })().then(sendResponse).catch(err => sendResponse({ok:false,error:String(err && (err.message || err))}));
    return true;
  }

  if (msg.type === 'NFSE_GET_PAYLOAD') {
    getNfsePayload().then(payload => sendResponse({ok:true,payload})).catch(err => sendResponse({ok:false,error:String(err)}));
    return true;
  }

  if (msg.type === 'NFSE_CLEAR_PAYLOAD') {
    chrome.storage.local.remove([NFSE_STORAGE_KEY,NFSE_SAVED_AT_KEY]).then(() => sendResponse({ok:true})).catch(err => sendResponse({ok:false,error:String(err)}));
    return true;
  }

  if (msg.type === 'NFSE_DRAFT_READY') {
    (async () => {
      const tabs = await chrome.tabs.query({url:['https://humiat.com.br/*','https://www.humiat.com.br/*']});
      let delivered = 0;
      for (const tab of tabs) {
        if (!tab.id) continue;
        try { await chrome.tabs.sendMessage(tab.id,{type:'NFSE_DRAFT_READY',rascunhoId:msg.rascunhoId||null}); delivered++; } catch (_) {}
      }
      return {ok:true,delivered};
    })().then(sendResponse).catch(err=>sendResponse({ok:false,error:String(err)}));
    return true;
  }

  if (msg.type === 'NFAE_MAIN_ACTION') {
    (async () => {
      if (!sender || !sender.tab || !sender.tab.id) return {ok:false, error:'aba não localizada'};
      return await executeMainAction(sender.tab.id, msg.action || '');
    })().then(sendResponse).catch(err => sendResponse({ok:false,error:String(err && (err.message || err))}));
    return true;
  }

  if (msg.type === 'NFAE_PREPARADO') {
    (async () => {
      if (!msg.payload) throw new Error('Payload da NFA-e não informado.');
      await savePayload(msg.payload);
      return await focusSefaz(msg.payload);
    })().then(sendResponse).catch(err => sendResponse({ok:false,error:String(err && (err.message || err))}));
    return true;
  }

  if (msg.type === 'NFAE_GET_PAYLOAD') {
    getPayload().then(payload => sendResponse({ok:true,payload})).catch(err => sendResponse({ok:false,error:String(err)}));
    return true;
  }

  if (msg.type === 'NFAE_CLEAR_PAYLOAD') {
    chrome.storage.local.remove([STORAGE_KEY, SAVED_AT_KEY]).then(() => sendResponse({ok:true})).catch(err => sendResponse({ok:false,error:String(err)}));
    return true;
  }


  if (msg.type === 'NFAE_WORKFLOW_BROADCAST') {
    (async () => {
      if (!sender || !sender.tab || !sender.tab.id) return {ok:false};
      const ok = await broadcastWorkflowToTab(sender.tab.id, msg.command || 'START', {
        checkpoint: msg.checkpoint || '',
        message: msg.message || '',
        detail: msg.detail || ''
      });
      return {ok};
    })().then(sendResponse).catch(err => sendResponse({ok:false,error:String(err)}));
    return true;
  }

  if (msg.type === 'NFAE_REFILL') {
    (async () => {
      const payload = await getPayload();
      if (sender && sender.tab && sender.tab.id && payload) {
        await sendPayloadToTab(sender.tab.id, payload);
      }
      return {ok:true};
    })().then(sendResponse).catch(err => sendResponse({ok:false,error:String(err)}));
    return true;
  }
});
