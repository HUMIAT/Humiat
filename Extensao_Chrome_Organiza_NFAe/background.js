const SEFAZ_MATCH = 'https://svp-extranet.svrs.rs.gov.br/*';
const SEFAZ_HOME = 'https://svp-extranet.svrs.rs.gov.br/';
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

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return;

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
