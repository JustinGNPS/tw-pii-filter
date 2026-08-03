/**
 * Service worker：只負責註冊右鍵選單，並把點擊轉給 content script。
 *
 * 偵測本身刻意**不放在這裡**。MV3 的 service worker 閒置約 30 秒就會被
 * Chrome 終止，之後每次喚醒都要重新初始化——之後接上語意層（NER 模型）時，
 * 這代表每次使用都要重新載入上百 MB 的模型，體驗直接崩掉。
 * 規則層很輕，直接跑在 content script 裡；語意層之後會走 offscreen document
 * （有完整 DOM、生命週期可控），不會放進 service worker。
 */

const MENU_ID = 'tw-pii-filter-process-selection';

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU_ID,
      title: '🛡 隱私處理後貼上',
      contexts: ['editable', 'selection'],
    });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== MENU_ID || !tab?.id) return;
  chrome.tabs.sendMessage(tab.id, {
    type: 'PROCESS_SELECTION',
    text: info.selectionText ?? '',
  });
});
