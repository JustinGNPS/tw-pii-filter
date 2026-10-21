/**
 * 還原：把 AI 回覆裡的佔位符換回真值（純字串邏輯，不碰 DOM）。
 *
 * 對應 Python 的 `core/redact/restorer.restore_text()`，兩個載體的規則一致：
 *
 *  - **佔位符格式**：`[大寫型別_序號]`，正則與 `core/redact/mapping.TOKEN_PATTERN` 相同
 *  - **查不到就原樣保留，絕不猜測**（B_design.md 決定 5）。AI 可能自己編出
 *    `[TW_ID_9]` 這種從沒發過的號碼；猜一個值填進去等於捏造個資，
 *    原樣留著使用者至少看得出「這裡有東西沒還原」。
 *
 * 對照表的來源是 {@link PlaceholderState}（遮蔽時就存在 chrome.storage.local），
 * 作用域與配號一樣是「單一對話」—— 別的對話的 `[NAME_1]` 是另一個人，
 * 絕不能拿來還原這個對話的 `[NAME_1]`。
 */

import type { PlaceholderState } from './placeholder';

/** 與 `core/redact/mapping.TOKEN_PATTERN` 相同。 */
export const TOKEN_PATTERN = /\[([A-Z][A-Z_]*)_(\d+)\]/g;

/** 佔位符 → 原文。 */
export type ReverseMap = Map<string, string>;

/**
 * 從配號狀態建出「佔位符 → 原文」的反查表。
 *
 * `assigned` 的 key 是 `${type}\t${原文}`、value 是序號（見 placeholder.ts），
 * 所以 `[${type}_${序號}]` 就是當初配出去的佔位符。原文本身可能含 `\t`，
 * 只切第一個。
 */
export function reverseMap(state: PlaceholderState | null | undefined): ReverseMap {
  const map: ReverseMap = new Map();
  if (!state) return map;
  for (const [key, index] of Object.entries(state.assigned)) {
    const tab = key.indexOf('\t');
    if (tab <= 0) continue;
    map.set(`[${key.slice(0, tab)}_${index}]`, key.slice(tab + 1));
  }
  return map;
}

export interface RestoreResult {
  text: string;
  /** 換回真值的出現次數 */
  restored: number;
  /** 長得像佔位符但查不到、原樣保留的出現次數 */
  unknown: number;
  /** 換進去的真值在**輸出字串**中的位置，用來標色 */
  segments: Array<{ start: number; end: number }>;
}

export function restoreString(text: string, map: ReverseMap): RestoreResult {
  let restored = 0;
  let unknown = 0;
  const segments: RestoreResult['segments'] = [];
  let out = '';
  let last = 0;

  for (const match of text.matchAll(TOKEN_PATTERN)) {
    const token = match[0];
    const start = match.index ?? 0;
    out += text.slice(last, start);
    const value = map.get(token);
    if (value === undefined) {
      unknown += 1;
      out += token;
    } else {
      restored += 1;
      segments.push({ start: out.length, end: out.length + value.length });
      out += value;
    }
    last = start + token.length;
  }
  out += text.slice(last);
  return { text: out, restored, unknown, segments };
}

/**
 * 這個網址是不是「還沒建立對話」的新對話頁。
 *
 * 三個網站在新對話送出第一則訊息之後才會配發對話 ID 並改網址：
 *
 *   ChatGPT  /     → /c/<id>
 *   Claude   /new  → /chat/<id>
 *   Gemini   /app  → /app/<id>
 *
 * 配號是以網址為 key 存的，所以在新對話頁貼上時存的那份狀態，
 * 網址一換就找不到了 —— 還原會整個失效。content script 用這個函式判斷
 * 「剛剛那份狀態其實屬於現在這個對話」，把它搬過去。
 *
 * 刻意只認這三個路徑：若放寬成「任何換頁都把狀態帶過去」，使用者從對話 A
 * 切到對話 B 時，A 的 `[NAME_1]` 會被拿去還原 B 的 `[NAME_1]` —— 換錯人
 * 比沒還原更糟。
 */
export function isNewChatPath(pathname: string): boolean {
  return pathname === '/' || pathname === '/new' || pathname === '/app';
}
