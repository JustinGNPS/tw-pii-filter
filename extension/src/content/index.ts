/**
 * Content script：攔在文字進入 AI 網頁輸入框的那一刻。
 *
 * ## 為什麼主要入口是 paste 事件，而不是右鍵選單
 *
 * 規劃書原本設定「右鍵選單 →『隱私處理後貼上』」。實作時發現兩個問題：
 *
 *  1. **權限**：右鍵選單拿不到剪貼簿內容，必須額外要 `clipboardRead` 權限，
 *     上架審查會被追問，也讓「本地端、最小權限」的訴求打折。
 *  2. **體驗**：使用者得改變習慣，多記一個操作。真正危險的情境恰恰是
 *     「沒多想就 Ctrl+V」——而右鍵選單保護不到沒多想的人。
 *
 * 攔截 paste 事件兩個問題都沒有：`event.clipboardData` 不需要任何權限，
 * 而且使用者照常 Ctrl+V 就會被保護，零學習成本。
 * 右鍵選單保留為次要入口，用來處理「已經打在輸入框裡」的文字。
 */

import { detectAll } from '../core';
import { maskText } from '../masking';
import {
  PlaceholderAllocator,
  conversationKey,
  loadState,
  saveState,
} from '../placeholder';
import { insertText, isEditable } from './insert';
import { showPanel } from './panel';

/** 防止我們自己插入的文字再次觸發攔截，造成無限迴圈。 */
let isInserting = false;

/** 同一時間只允許一個面板，避免連續貼上時疊出多個對話框。 */
let panelOpen = false;

async function handleText(element: HTMLElement, text: string): Promise<void> {
  const { spans } = detectAll(text);

  // 沒偵測到東西就完全不打擾使用者——這是擴充能被長期留著的前提
  if (spans.length === 0) {
    isInserting = true;
    insertText(element, text);
    isInserting = false;
    return;
  }

  // 以「這個對話」為單位載入配號狀態，讓同一個真值在多輪之間拿到同一個佔位符
  const storageKey = conversationKey(new URL(location.href));
  const state = await loadState(storageKey);

  // 面板上顯示的佔位符要用「預覽」配號器算：使用者可能取消勾選某些項目，
  // 直接在正式配號器上配會留下用不到的號碼缺口。
  const preview = new PlaceholderAllocator(structuredClone(state));
  const previewPlaceholders = spans.map((span) => preview.allocate(span.type, span.text));

  panelOpen = true;
  let decision;
  try {
    decision = await showPanel(spans, previewPlaceholders);
  } finally {
    panelOpen = false;
  }

  if (decision.action === 'cancel') return;

  isInserting = true;
  try {
    if (decision.action === 'raw') {
      insertText(element, text);
      return;
    }

    // 正式配號只針對使用者實際勾選的項目
    const allocator = new PlaceholderAllocator(state);
    const { maskedText, mapping } = maskText(text, decision.spans, allocator);
    insertText(element, maskedText);

    void saveState(storageKey, allocator.toState());
    void recordMapping(mapping);
  } finally {
    isInserting = false;
  }
}

/**
 * 把對照表存進本地 storage，供第二版的還原機制使用。
 *
 * ⚠️ 對照表本身就是一份集中的明文個資，是攻擊面。目前先明確標記
 * 資料結構與時間戳，加密與自動過期留待第二版還原機制一起處理
 * （見專題報告 7.2 節「對照表是攻擊面」）。這份資料絕不外傳。
 */
async function recordMapping(mapping: ReturnType<typeof maskText>['mapping']): Promise<void> {
  if (mapping.length === 0) return;
  try {
    const { sessions = [] } = await chrome.storage.local.get('sessions');
    sessions.push({ at: Date.now(), host: location.host, mapping });
    await chrome.storage.local.set({ sessions: sessions.slice(-50) });
  } catch (error) {
    console.warn('[tw-pii-filter] 對照表寫入失敗', error);
  }
}

document.addEventListener(
  'paste',
  (event) => {
    if (isInserting || panelOpen) return;

    const element = event.target;
    if (!isEditable(element)) return;

    const text = event.clipboardData?.getData('text/plain');
    if (!text || text.trim().length === 0) return;

    // 先攔下原生貼上；後續由我們決定要放原文還是遮蔽版
    event.preventDefault();
    event.stopPropagation();
    void handleText(element, text);
  },
  true, // capture 階段：搶在頁面自己的 paste handler（如 ProseMirror）之前
);

/**
 * 次要入口：右鍵選單「隱私處理後貼上」。
 * background 收到選單點擊後把選取文字送過來，這裡就地處理。
 */
chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== 'PROCESS_SELECTION') return;
  if (panelOpen) return;

  const element = document.activeElement;
  if (!isEditable(element)) return;

  const text = window.getSelection()?.toString() || message.text;
  if (!text) return;

  void handleText(element, text);
});
