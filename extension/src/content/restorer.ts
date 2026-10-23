/**
 * 安全回覆檢視器：辨識 AI 網頁上的佔位符，但不把明文寫回網站 DOM。
 *
 * 明文只會交給擴充建立的 closed Shadow DOM 顯示。網站本身始終保留
 * `[NAME_1]` 這類佔位符，避免頁面腳本、分析 SDK 或 React 狀態重新取得真值。
 *
 * ## 絕不碰輸入框
 *
 * 使用者貼上後輸入框裡是遮蔽版，那正是要送出去的內容。若把輸入框也還原，
 * 送出的就是真值，前面的遮蔽全部白做。所以 textarea / input / contenteditable
 * 一律跳過 —— 這是這個模組最重要的一條規則，有測試釘住。
 */

import { restoreString, type ReverseMap } from '../restore';

const SKIP_SELECTOR = 'textarea, input, script, style, noscript, [contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"]';

/** 這個 Text 節點能不能動。輸入框、程式碼標籤、我們自己的 UI 都不能。 */
export function isRestorableTextNode(node: Node, ownHost: Element | null): boolean {
  const parent = node.parentElement;
  if (!parent) return false;
  if (parent.closest(SKIP_SELECTOR)) return false;
  if (parent.isContentEditable) return false;
  if (ownHost && ownHost.contains(parent)) return false;
  return true;
}

interface Touched {
  /** 網站 DOM 裡的佔位符版本。 */
  masked: string;
  /** 只在 closed Shadow DOM 顯示的本機還原版本。 */
  restored: string;
  restoredCount: number;
}

export class PageRestorer {
  private observer: MutationObserver | null = null;
  private touched = new Map<Text, Touched>();
  private pending = new Set<Node>();
  private flushTimer: number | null = null;
  private map: ReverseMap = new Map();
  private onChange: (restoredCount: number, snippets: string[]) => void;
  private ownHost: Element | null;
  private root: Node;

  /**
   * @param root 只處理這個節點底下的文字。擴充用整頁（預設）；展示網站只還原
   *             模擬的聊天視窗，不能動到旁邊刻意顯示佔位符的「AI 收到的版本」欄位。
   */
  constructor(
    ownHost: Element | null,
    onChange: (restoredCount: number, snippets: string[]) => void,
    root: Node = document.body,
  ) {
    this.ownHost = ownHost;
    this.onChange = onChange;
    this.root = root;
  }

  get enabled(): boolean {
    return this.observer !== null;
  }

  /** 換一份對照表（換對話、或剛遮蔽了新的值），開著的話立刻重掃。 */
  setMap(map: ReverseMap): void {
    this.map = map;
    this.touched.clear();
    if (this.enabled) this.sweep(this.root);
  }

  enable(): void {
    if (this.enabled) return;
    this.observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'characterData') this.pending.add(record.target);
        record.addedNodes.forEach((node) => this.pending.add(node));
      }
      this.scheduleFlush();
    });
    this.observer.observe(this.root, { subtree: true, childList: true, characterData: true });
    this.sweep(this.root);
  }

  /** 關掉本機檢視器；網站 DOM 從頭到尾都不需要改動。 */
  disable(): void {
    this.observer?.disconnect();
    this.observer = null;
    if (this.flushTimer !== null) window.clearTimeout(this.flushTimer);
    this.flushTimer = null;
    this.pending.clear();
    this.touched.clear();
    this.onChange(0, []);
  }

  private scheduleFlush(): void {
    if (this.flushTimer !== null) return;
    // 串流時 DOM 每秒改幾十次，合併成一批處理
    this.flushTimer = window.setTimeout(() => {
      this.flushTimer = null;
      const nodes = [...this.pending];
      this.pending.clear();
      for (const node of nodes) if (node.isConnected) this.sweep(node);
    }, 80);
  }

  private sweep(root: Node): void {
    if (this.map.size === 0) {
      this.report();
      return;
    }
    if (root.nodeType === Node.TEXT_NODE) {
      this.restoreNode(root as Text);
    } else {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) this.restoreNode(n as Text);
    }
    this.report();
  }

  private restoreNode(node: Text): void {
    const value = node.nodeValue;
    if (!value || !value.includes('[') || !isRestorableTextNode(node, this.ownHost)) {
      this.touched.delete(node);
      return;
    }

    const result = restoreString(value, this.map);
    if (result.restored === 0) {
      this.touched.delete(node);
      return;
    }

    // 安全邊界：只記錄結果，不做 node.nodeValue = result.text。
    this.touched.set(node, {
      masked: value,
      restored: result.text,
      restoredCount: result.restored,
    });
  }

  private report(): void {
    let count = 0;
    const snippets: string[] = [];
    const seen = new Set<string>();
    for (const [node, info] of this.touched) {
      if (!node.isConnected || node.nodeValue !== info.masked) {
        this.touched.delete(node);
        continue;
      }
      count += info.restoredCount;
      if (!seen.has(info.restored)) {
        seen.add(info.restored);
        snippets.push(info.restored);
      }
    }
    this.onChange(count, snippets);
  }
}

const TOGGLE_STYLE = `
  :host { all: initial; }
  button {
    position: fixed; right: 16px; bottom: 96px; z-index: 2147483646;
    display: flex; align-items: center; gap: 6px;
    padding: 7px 12px; border-radius: 999px; border: 1px solid #cbd5e1;
    background: #fff; color: #0f172a; cursor: pointer;
    font: 600 12.5px -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
    box-shadow: 0 4px 14px rgba(15, 23, 42, .15);
  }
  button.on { border-color: #10b981; }
  button:hover { background: #f8fafc; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; }
  button.on .dot { background: #10b981; }
  .hint { font-weight: 400; color: #64748b; }
  .panel {
    position: fixed; right: 16px; bottom: 140px; z-index: 2147483646;
    width: min(520px, calc(100vw - 32px)); max-height: min(55vh, 520px);
    overflow: auto; box-sizing: border-box; padding: 14px;
    border: 1px solid #5eead4; border-radius: 12px;
    background: #f8fafc; color: #0f172a;
    box-shadow: 0 12px 35px rgba(15, 23, 42, .24);
    font: 13px/1.55 -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
  }
  .panel[hidden] { display: none; }
  .title { margin: 0 0 8px; color: #0f766e; font-weight: 700; }
  .notice { margin: 0 0 10px; color: #475569; font-size: 12px; }
  pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
  .snippet + .snippet { border-top: 1px solid #cbd5e1; margin-top: 10px; padding-top: 10px; }
`;

/**
 * 右下角的小開關。只有這個對話有對照表時才出現 ——
 * 沒遮蔽過任何東西的對話，不該多一顆按鈕干擾使用者。
 */
export class RestoreToggle {
  readonly host: HTMLElement;
  private button: HTMLButtonElement;
  private panel: HTMLElement;
  private snippets: HTMLElement;

  constructor(onClick: () => void) {
    this.host = document.createElement('tw-pii-restore-toggle');
    const root = this.host.attachShadow({ mode: 'closed' });
    const style = document.createElement('style');
    style.textContent = TOGGLE_STYLE;
    this.button = document.createElement('button');
    this.button.addEventListener('click', onClick);
    this.panel = document.createElement('section');
    this.panel.className = 'panel';
    const title = document.createElement('p');
    title.className = 'title';
    title.textContent = '🛡 本機還原檢視器';
    const notice = document.createElement('p');
    notice.className = 'notice';
    notice.textContent = '真值只顯示在擴充套件的封閉區域；AI 網頁仍保留佔位符。';
    this.snippets = document.createElement('div');
    this.panel.append(title, notice, this.snippets);
    root.append(style, this.panel, this.button);
    this.host.hidden = true;
    document.documentElement.appendChild(this.host);
  }

  render(visible: boolean, enabled: boolean, restoredCount: number, snippets: string[] = []): void {
    this.host.hidden = !visible;
    this.host.style.display = visible ? '' : 'none';
    this.button.className = enabled ? 'on' : '';
    this.button.textContent = enabled
      ? `🛡 本機檢視 ${restoredCount} 處 · 點我關閉`
      : '👁 AI 頁面顯示佔位符 · 點我本機還原';
    this.button.title = '明文只在擴充套件的封閉檢視器顯示，不寫回 AI 網頁 DOM';
    this.panel.hidden = !enabled || snippets.length === 0;
    this.snippets.replaceChildren(...snippets.map((text) => {
      const pre = document.createElement('pre');
      pre.className = 'snippet';
      pre.textContent = text;
      return pre;
    }));
  }
}
