const SEFAZ_MATCH = 'https://svp-extranet.svrs.rs.gov.br/*';
const SEFAZ_HOME = 'https://svp-extranet.svrs.rs.gov.br/';

async function focusSefaz() {
  const tabs = await chrome.tabs.query({url: SEFAZ_MATCH});
  if (tabs && tabs.length) {
    const tab = tabs.find(t => t.active) || tabs[0];
    if (tab.windowId) await chrome.windows.update(tab.windowId, {focused: true});
    if (tab.id) {
      await chrome.tabs.update(tab.id, {active: true});
      try { await chrome.tabs.sendMessage(tab.id, {type: 'NFAE_PREENCHER'}); } catch (_) {}
    }
    return {found: true, tabId: tab.id};
  }
  const tab = await chrome.tabs.create({url: SEFAZ_HOME, active: true});
  return {found: false, tabId: tab.id};
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return;
  if (msg.type === 'NFAE_PREPARADO') {
    focusSefaz().then(sendResponse).catch(err => sendResponse({ok:false,error:String(err)}));
    return true;
  }
  if (msg.type === 'NFAE_REFILL') {
    if (sender && sender.tab && sender.tab.id) {
      chrome.tabs.sendMessage(sender.tab.id, {type:'NFAE_PREENCHER'}).catch(()=>{});
    }
    sendResponse({ok:true});
  }
});
