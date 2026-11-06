/**
 * Service worker：只負責註冊右鍵選單，並把點擊轉給 content script。
 *
 * 偵測本身刻意**不放在這裡**。MV3 的 service worker 閒置約 30 秒就會被
 * Chrome 終止，之後每次喚醒都要重新初始化——之後接上語意層（NER 模型）時，
 * 這代表每次使用都要重新載入上百 MB 的模型，體驗直接崩掉。
 * 規則層很輕，直接跑在 content script 裡；語意層之後會走 offscreen document
 * （有完整 DOM、生命週期可控），不會放進 service worker。
 */

import { SEMANTIC_MESSAGE } from '../semantic';
import { fetchSemanticPayload } from './semantic_fetch';

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

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== SEMANTIC_MESSAGE) return false;
  if (typeof message.text !== 'string') {
    sendResponse({ ok: false, error: '語意分析文字格式錯誤' });
    return false;
  }

  void fetchSemanticPayload(message.text)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((error: unknown) => {
      const messageText = error instanceof Error ? error.message : '無法連線本機語意服務';
      // 不把原文寫入 console；錯誤只包含連線或設定狀態。
      console.warn('[tw-pii-filter] 本機語意分析失敗：', messageText);
      sendResponse({ ok: false, error: messageText });
    });
  return true;
});
