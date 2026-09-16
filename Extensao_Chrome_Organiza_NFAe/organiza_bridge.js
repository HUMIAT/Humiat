(function(){
  function notify(type, extra={}) {
    window.postMessage(Object.assign({type}, extra), '*');
  }

  function extensionAlive() {
    try { return !!(chrome && chrome.runtime && chrome.runtime.id); } catch (_) { return false; }
  }

  async function prepareNfse(payloadUrl) {
    try {
      const url = new URL(payloadUrl, location.origin).toString();
      const resp = await fetch(url, {credentials:'include', cache:'no-store'});
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      if (data.campos_faltantes && data.campos_faltantes.length) throw new Error('Faltam campos: ' + data.campos_faltantes.join(', '));
      if (!extensionAlive()) throw new Error('A extensão foi atualizada. Atualize esta aba do Organiza e tente novamente.');
      const answer = await chrome.runtime.sendMessage({type:'NFSE_PREPARADO', payload:data});
      if (answer && answer.ok === false) throw new Error(answer.error || 'Falha ao preparar NFS-e.');
      notify('ORGANIZA_NFSE_OK');
    } catch (err) {
      notify('ORGANIZA_NFSE_ERRO', {mensagem: String(err && err.message ? err.message : err)});
    }
  }

  async function prepare(payloadUrl) {
    try {
      const url = new URL(payloadUrl, location.origin).toString();
      const resp = await fetch(url, {credentials:'include', cache:'no-store'});
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      if (data.campos_faltantes && data.campos_faltantes.length) {
        throw new Error('Faltam campos: ' + data.campos_faltantes.join(', '));
      }
      if (!extensionAlive()) throw new Error('A extensão foi atualizada. Atualize esta aba do Organiza e tente novamente.');
      const answer = await chrome.runtime.sendMessage({type:'NFAE_PREPARADO', payload:data});
      if (answer && answer.ok === false) throw new Error(answer.error || 'Falha ao preparar NFA-e.');
      notify('ORGANIZA_NFAE_OK');
    } catch (err) {
      notify('ORGANIZA_NFAE_ERRO', {mensagem: String(err && err.message ? err.message : err)});
    }
  }

  try {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg && msg.type === 'NFSE_DRAFT_READY') notify('ORGANIZA_NFSE_DRAFT_READY', {rascunhoId: msg.rascunhoId || null});
    });
  } catch (_) {}

  window.addEventListener('message', (ev) => {
    if (ev.source !== window || !ev.data) return;
    if (ev.data.type === 'ORGANIZA_NFAE_PING') notify('ORGANIZA_NFAE_EXT_READY');
    if (ev.data.type === 'ORGANIZA_NFSE_PING') notify('ORGANIZA_NFSE_EXT_READY');
    if (ev.data.type === 'ORGANIZA_NFAE_PREPARAR' && ev.data.payloadUrl) prepare(ev.data.payloadUrl);
    if (ev.data.type === 'ORGANIZA_NFSE_PREPARAR' && ev.data.payloadUrl) prepareNfse(ev.data.payloadUrl);
  });

  notify('ORGANIZA_NFAE_EXT_READY');
  notify('ORGANIZA_NFSE_EXT_READY');
})();
