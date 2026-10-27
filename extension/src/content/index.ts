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
  conversationKey,
  PlaceholderAllocator,
  purgeStoredMappings,
} from '../placeholder';
import { isNewChatPath, reverseMap } from '../restore';
import { filterEnabled, loadEnabledTypes } from '../settings';
import { findNearMisses } from '../explain';
import { showNearMissHint } from './hint';
import { editableRoot, insertText, isEditable } from './insert';
import { showPanel } from './panel';
import { PageRestorer, RestoreToggle } from './restorer';
import { ConversationStateStore } from './conversation_state';
import { shouldMarkFromControl, shouldMarkFromKeydown } from './submission_guard';

/** 防止我們自己插入的文字再次觸發攔截，造成無限迴圈。 */
let isInserting = false;

/** 同一時間只允許一個面板，避免連續貼上時疊出多個對話框。 */
let panelOpen = false;

/** 每個分頁各自一份；新對話尚無 ID 時絕不寫入跨分頁共用的 storage key。 */
const stateStore = new ConversationStateStore();

/** 這次新對話中，實際被插入遮蔽文字的聊天輸入框。 */
let pendingNewChatInput: HTMLElement | null = null;

async function handleText(element: HTMLElement, text: string): Promise<void> {
  element = editableRoot(element);
  const detected = detectAll(text);
  // 使用者在 popup 關掉的類型當作沒偵測到（見 settings.ts）
  const spans = filterEnabled(detected.spans, await loadEnabledTypes());
  const { combination_risk } = detected;
  // 格式像、但檢核碼不對的號碼：照原文貼上，但要讓使用者知道我們看到了（見 explain.ts）
  const nearMisses = findNearMisses(text, detected.spans);
  const risk = combination_risk ?? null;

  // 沒偵測到東西、也沒有組合風險，就完全不打擾使用者
  // ——這是擴充能被長期留著的前提。
  //
  // ⚠️ 條件刻意包含組合風險：像「35歲男性，住新竹，資深後端工程師」這種文字
  // 一個 span 都抓不到，但正是報告 4.3 講的「隱含身分」情境（洩漏率 95%）。
  // 若只看 spans 就放行，面板等於告訴使用者「沒問題」——那比不提醒更糟。
  if (spans.length === 0 && !risk) {
    isInserting = true;
    insertText(element, text);
    isInserting = false;
    showNearMissHint(nearMisses);
    return;
  }

  // 以「這個對話」為單位載入配號狀態，讓同一個真值在多輪之間拿到同一個佔位符
  const currentUrl = new URL(location.href);
  const state = await stateStore.load(currentUrl);

  // 面板上顯示的佔位符要用「預覽」配號器算：使用者可能取消勾選某些項目，
  // 直接在正式配號器上配會留下用不到的號碼缺口。
  const preview = new PlaceholderAllocator(structuredClone(state));
  const previewPlaceholders = spans.map((span) => preview.allocate(span.type, span.text));

  panelOpen = true;
  let decision;
  try {
    decision = await showPanel(spans, previewPlaceholders, risk);
  } finally {
    panelOpen = false;
  }

  if (decision.action === 'cancel') return;
  showNearMissHint(nearMisses);

  isInserting = true;
  try {
    if (decision.action === 'raw') {
      insertText(element, text);
      return;
    }

    // 正式配號只針對使用者實際勾選的項目
    const allocator = new PlaceholderAllocator(state);
    const { maskedText } = maskText(text, decision.spans, allocator);
    insertText(element, maskedText);

    await stateStore.save(currentUrl, allocator.toState());
    if (isNewChatPath(currentUrl.pathname)) pendingNewChatInput = element;
    void refreshRestore();
  } finally {
    isInserting = false;
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

/**
 * 新對話暫存只有在使用者真的送出第一則訊息後，才能跟著新的對話網址搬移。
 * 只看 URL 變化無法分辨「送出後取得 ID」和「從側欄點進舊對話」。
 */
function markNewChatSubmitted(): void {
  stateStore.markNewChatSubmitted(new URL(location.href));
}

function pendingInputHasText(): boolean {
  const input = pendingNewChatInput;
  if (!input || !input.isConnected) return false;
  if (input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) {
    return input.value.trim().length > 0;
  }
  return (input.textContent ?? '').trim().length > 0;
}

function eventComesFromPendingInput(target: EventTarget | null): boolean {
  return target instanceof Node
    && pendingNewChatInput !== null
    && (target === pendingNewChatInput || pendingNewChatInput.contains(target));
}

function sharesPendingComposer(control: HTMLElement): boolean {
  const input = pendingNewChatInput;
  if (!input || !input.isConnected) return false;

  const form = input.closest('form');
  if (form) return form.contains(control);

  let ancestor = input.parentElement;
  for (let depth = 0; ancestor && depth < 6; depth += 1) {
    if (ancestor === document.body) return false;
    if (ancestor.contains(control)) return true;
    ancestor = ancestor.parentElement;
  }
  return false;
}

document.addEventListener(
  'submit',
  (event) => {
    if (
      event.target instanceof HTMLFormElement
      && pendingNewChatInput !== null
      && event.target.contains(pendingNewChatInput)
      && pendingInputHasText()
    ) markNewChatSubmitted();
  },
  true,
);

document.addEventListener(
  'keydown',
  (event) => {
    if (shouldMarkFromKeydown(
      event,
      eventComesFromPendingInput(event.target),
      pendingInputHasText(),
    )) markNewChatSubmitted();
  },
  true,
);

document.addEventListener(
  'click',
  (event) => {
    if (!(event.target instanceof Element)) return;
    const control = event.target.closest<HTMLElement>('button, [role="button"]');
    if (!control) return;

    const hint = [
      control.getAttribute('aria-label'),
      control.getAttribute('title'),
      control.getAttribute('data-testid'),
      control.getAttribute('name'),
    ].filter(Boolean).join(' ').toLowerCase();
    if (shouldMarkFromControl(
      {
        disabled: control.getAttribute('aria-disabled') === 'true'
          || (control instanceof HTMLButtonElement && control.disabled),
        tagName: control.tagName,
        type: control.getAttribute('type'),
        hint,
      },
      sharesPendingComposer(control),
      pendingInputHasText(),
    )) markNewChatSubmitted();
  },
  true,
);

// ─────────────────────────────────────────────────────────────
// 還原：AI 回覆裡的佔位符在本地換回真值（雲端從頭到尾只看到佔位符）
// ─────────────────────────────────────────────────────────────

const RESTORE_PREF_KEY = 'restoreEnabled';

let restoreEnabled = true;
let lastHref = location.href;
let mapSize = 0;
let restoredCount = 0;
let restoredSnippets: string[] = [];

const toggle = new RestoreToggle(() => void onToggleClick());
const restorer = new PageRestorer(toggle.host, (count, snippets) => {
  restoredCount = count;
  restoredSnippets = snippets;
  toggle.render(mapSize > 0, restorer.enabled, restoredCount, restoredSnippets);
});

/** 重新載入「這個對話」的對照表，並依開關狀態套用。 */
async function refreshRestore(): Promise<void> {
  const state = await stateStore.load(new URL(location.href));
  const map = reverseMap(state);
  mapSize = map.size;
  restorer.setMap(map);
  if (restoreEnabled && mapSize > 0) restorer.enable();
  toggle.render(mapSize > 0, restorer.enabled, restoredCount, restoredSnippets);
}

async function onToggleClick(): Promise<void> {
  restoreEnabled = !restoreEnabled;
  if (restoreEnabled) restorer.enable();
  else restorer.disable();
  toggle.render(mapSize > 0, restorer.enabled, restoredCount, restoredSnippets);
  try {
    await chrome.storage.local.set({ [RESTORE_PREF_KEY]: restoreEnabled });
  } catch {
    /* 偏好設定存不進去不影響功能 */
  }
}

/**
 * 新對話送出第一則訊息後網址會從 `/` 變成 `/c/<id>`（各站寫法見 isNewChatPath），
 * 在新對話頁存下的配號要跟著搬到新網址，否則還原會找不到對照表。
 *
 * 三個條件全部成立才搬，任何一個不成立都寧可不還原，也不要拿錯人的對照表：
 *  1. 舊網址是新對話頁
 *  2. 舊網址那份狀態是**這個分頁**剛存的（不是別的分頁留下的）
 *  3. 新網址還沒有自己的狀態
 */
async function adoptNewChatState(prev: URL, next: URL): Promise<void> {
  await stateStore.adoptNewChatState(prev, next);
  if (!isNewChatPath(next.pathname)) pendingNewChatInput = null;
}

async function initRestore(): Promise<void> {
  await purgeStoredMappings();
  try {
    const stored = await chrome.storage.local.get(RESTORE_PREF_KEY);
    if (stored[RESTORE_PREF_KEY] === false) restoreEnabled = false;
  } catch {
    /* 讀不到就用預設（開啟） */
  }
  await refreshRestore();

  // 三個網站都是單頁應用，換對話不會重新載入頁面，只能輪詢網址
  window.setInterval(() => {
    if (location.href === lastHref) return;
    const prev = new URL(lastHref);
    lastHref = location.href;
    void adoptNewChatState(prev, new URL(lastHref)).then(refreshRestore);
  }, 1000);

  // 同一個對話開在兩個分頁時，另一邊遮蔽的新值這邊也要能還原
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (conversationKey(new URL(location.href)) in changes) void refreshRestore();
  });
}

void initRestore();
