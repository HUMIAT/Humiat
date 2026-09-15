(function(){
  const KEY = 'organiza_nfae_payload';

  function notify(type, extra={}) {
    window.postMessage(Object.assign({type}, extra), '*');
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
      await chrome.storage.local.set({
        [KEY]: data,
        organiza_nfae_saved_at: Date.now()
      });
      notify('ORGANIZA_NFAE_OK');
      chrome.runtime.sendMessage({type:'NFAE_PREPARADO'}).catch(()=>{});
    } catch (err) {
      notify('ORGANIZA_NFAE_ERRO', {mensagem: String(err && err.message ? err.message : err)});
    }
  }

  window.addEventListener('message', (ev) => {
    if (ev.source !== window || !ev.data) return;
    if (ev.data.type === 'ORGANIZA_NFAE_PING') {
      notify('ORGANIZA_NFAE_EXT_READY');
    }
    if (ev.data.type === 'ORGANIZA_NFAE_PREPARAR' && ev.data.payloadUrl) {
      prepare(ev.data.payloadUrl);
    }
  });

  notify('ORGANIZA_NFAE_EXT_READY');
})();
