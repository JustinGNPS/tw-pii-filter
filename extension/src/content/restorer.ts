/**
 * 在 AI 網頁上把佔位符換回真值，並提供「切換成 AI 看到的版本」的開關。
 *
 * ## 為什麼只改 Text 節點的內容，不拆節點、不包 <mark>
 *
 * ChatGPT / Claude / Gemini 的對話區都是 React 管的。React 手上握著每個
 * Text 節點的參考，之後串流更新時直接改它的 nodeValue。如果我們把節點拆開
 * 或包一層 <mark>，React 改到的是已經被拆掉的那半，畫面會出現重複或殘缺的字。
 * 只改 nodeValue 則與 React 自己的更新方式相同，下一次 React 覆寫回佔位符時，
 * MutationObserver 會再還原一次。
 *
 * 標色改用 CSS Custom Highlight API（`CSS.highlights`）：只登記 Range，
 * 完全不動 DOM 結構。不支援的瀏覽器就只還原、不標色。
 *
 * ## 絕不碰輸入框
 *
 * 使用者貼上後輸入框裡是遮蔽版，那正是要送出去的內容。若把輸入框也還原，
 * 送出的就是真值，前面的遮蔽全部白做。所以 textarea / input / contenteditable
 * 一律跳過 —— 這是這個模組最重要的一條規則，有測試釘住。
 */

import { restoreString, type ReverseMap } from '../restore';

const HIGHLIGHT_NAME = 'tw-pii-restored';
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
  /** 我們改之前的內容（佔位符版本） */
  masked: string;
  /** 我們寫進去的內容（真值版本） */
  restored: string;
}

export class PageRestorer {
  private observer: MutationObserver | null = null;
  private touched = new Map<Text, Touched>();
  private pending = new Set<Node>();
  private flushTimer: number | null = null;
  private highlight: Highlight | null = null;
  private map: ReverseMap = new Map();
  private onChange: (restoredCount: number) => void;
  private ownHost: Element | null;

  constructor(ownHost: Element | null, onChange: (restoredCount: number) => void) {
    this.ownHost = ownHost;
    this.onChange = onChange;
  }

  get enabled(): boolean {
    return this.observer !== null;
  }

  /** 換一份對照表（換對話、或剛遮蔽了新的值），開著的話立刻重掃。 */
  setMap(map: ReverseMap): void {
    this.map = map;
    if (this.enabled) this.sweep(document.body);
  }

  enable(): void {
    if (this.enabled) return;
    this.ensureHighlight();
    this.observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'characterData') this.pending.add(record.target);
        record.addedNodes.forEach((node) => this.pending.add(node));
      }
      this.scheduleFlush();
    });
    this.observer.observe(document.body, { subtree: true, childList: true, characterData: true });
    this.sweep(document.body);
  }

  /** 關掉並把畫面換回佔位符（AI 實際看到的版本）。 */
  disable(): void {
    this.observer?.disconnect();
    this.observer = null;
    if (this.flushTimer !== null) window.clearTimeout(this.flushTimer);
    this.flushTimer = null;
    this.pending.clear();
    for (const [node, info] of this.touched) {
      // React 已經改過的節點不要去動，只換回「還是我們寫的那個樣子」的
      if (node.isConnected && node.nodeValue === info.restored) node.nodeValue = info.masked;
    }
    this.touched.clear();
    this.highlight?.clear();
    this.onChange(0);
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
    if (!value || !value.includes('[')) return;
    if (!isRestorableTextNode(node, this.ownHost)) return;

    const result = restoreString(value, this.map);
    if (result.restored === 0) return;

    node.nodeValue = result.text;
    this.touched.set(node, { masked: value, restored: result.text });
    if (this.highlight) {
      for (const seg of result.segments) {
        const range = document.createRange();
        range.setStart(node, seg.start);
        range.setEnd(node, seg.end);
        this.highlight.add(range);
      }
    }
  }

  private report(): void {
    let count = 0;
    for (const [node, info] of this.touched) {
      if (!node.isConnected || node.nodeValue !== info.restored) {
        this.touched.delete(node);
        continue;
      }
      count += restoreString(info.masked, this.map).restored;
    }
    this.onChange(count);
  }

  private ensureHighlight(): void {
    if (this.highlight || typeof CSS === 'undefined' || !('highlights' in CSS)) return;
    this.highlight = new Highlight();
    CSS.highlights.set(HIGHLIGHT_NAME, this.highlight);
    const style = document.createElement('style');
    style.textContent = `::highlight(${HIGHLIGHT_NAME}) { background-color: rgba(16, 185, 129, .25); }`;
    document.head.appendChild(style);
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
`;

/**
 * 右下角的小開關。只有這個對話有對照表時才出現 ——
 * 沒遮蔽過任何東西的對話，不該多一顆按鈕干擾使用者。
 */
export class RestoreToggle {
  readonly host: HTMLElement;
  private button: HTMLButtonElement;

  constructor(onClick: () => void) {
    this.host = document.createElement('tw-pii-restore-toggle');
    const root = this.host.attachShadow({ mode: 'closed' });
    const style = document.createElement('style');
    style.textContent = TOGGLE_STYLE;
    this.button = document.createElement('button');
    this.button.addEventListener('click', onClick);
    root.append(style, this.button);
    this.host.hidden = true;
    document.documentElement.appendChild(this.host);
  }

  render(visible: boolean, enabled: boolean, restoredCount: number): void {
    this.host.hidden = !visible;
    this.host.style.display = visible ? '' : 'none';
    this.button.className = enabled ? 'on' : '';
    this.button.innerHTML = enabled
      ? `<span class="dot"></span>🛡 已還原 ${restoredCount} 處 <span class="hint">· 點我看 AI 收到的版本</span>`
      : `<span class="dot"></span>👁 目前顯示 AI 收到的版本 <span class="hint">· 點我還原</span>`;
    this.button.title = '本地端敏感資訊過濾：AI 回覆裡的佔位符在你的電腦上換回真值，雲端從頭到尾只看到佔位符';
  }
}
