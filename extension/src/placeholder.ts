/**
 * 佔位符配號器。
 *
 * ## 為什麼不能直接用 detect_all() 給的 replacement
 *
 * `detect_all()` 每次呼叫都從 1 重新編號。同一個真值這次可能是 `[TW_ID_1]`、
 * 下次變 `[TW_ID_2]`，而 `[TW_ID_1]` 還可能被別的真值佔用。使用者在同一個
 * 對話裡連續貼兩次，還原時就會**把兩個人的個資對調**——這比單純洩漏更嚴重。
 *
 * （這個問題由 B 在 PR #3 提出，proxy 端用同樣的作法規避：自己維護對照表發號碼，
 * 只採用 A 的 `type` 與座標，不用 `replacement` 的序號。）
 *
 * 本模組做的就是「同一個真值永遠對到同一個佔位符」的配號：
 * 一個真值第一次出現時配一個號，之後在同一個作用域內永遠是那個號。
 *
 * ## 作用域刻意限定在「單一對話」，不是全域永久
 *
 * 對話**之內**要一致：AI 才知道多輪之間的 `[PERSON_1]` 是同一個人。
 * 對話**之間**反而不該一致：
 *   - 穩定的假名跨情境流通，等於自己造了一個可追蹤的識別子
 *   - 對照表是集中的明文個資、是攻擊面（專題報告 7.2），不該無限增長
 *
 * 因此配號狀態以對話為單位儲存，並設保留期限自動過期。
 */

import type { PiiType } from './core';

/** 可序列化的配號狀態，存進 chrome.storage.local。 */
export interface PlaceholderState {
  /** `${type}\t${原文}` → 已配發的序號 */
  assigned: Record<string, number>;
  /** type → 該類別目前配到第幾號 */
  counters: Record<string, number>;
  /** 最後更新時間，用於過期清理 */
  updatedAt: number;
}

export function emptyState(): PlaceholderState {
  return { assigned: {}, counters: {}, updatedAt: Date.now() };
}

export class PlaceholderAllocator {
  private assigned: Map<string, number>;
  private counters: Map<string, number>;

  constructor(state: PlaceholderState = emptyState()) {
    this.assigned = new Map(Object.entries(state.assigned));
    this.counters = new Map(Object.entries(state.counters));
  }

  /**
   * 取得某個真值對應的佔位符；同一個 (type, 原文) 永遠回傳同一個。
   *
   * 注意 key 用 `type` + 原文，不是只用原文——不同類別碰巧有相同字串時
   * （例如某個 8 碼數字同時被判為統編與其他類別）應該分開配號。
   */
  allocate(type: PiiType, original: string): string {
    const key = `${type}\t${original}`;
    let index = this.assigned.get(key);
    if (index === undefined) {
      index = (this.counters.get(type) ?? 0) + 1;
      this.counters.set(type, index);
      this.assigned.set(key, index);
    }
    return `[${type}_${index}]`;
  }

  /** 目前配出去的佔位符數量（測試與統計用）。 */
  get size(): number {
    return this.assigned.size;
  }

  toState(): PlaceholderState {
    return {
      assigned: Object.fromEntries(this.assigned),
      counters: Object.fromEntries(this.counters),
      updatedAt: Date.now(),
    };
  }
}

/** 配號狀態的保留期限：超過就丟棄重新開始，避免對照表無限累積。 */
export const STATE_TTL_MS = 12 * 60 * 60 * 1000; // 12 小時

/**
 * 以「對話」為單位的儲存 key。
 *
 * ChatGPT / Claude / Gemini 的對話網址都帶有對話 ID（例如 /c/<uuid>），
 * 用 host + pathname 當 key，換一個對話就換一組配號，符合上面的作用域設計。
 */
export function conversationKey(url: URL): string {
  return `ph:${url.host}${url.pathname}`;
}

export async function loadState(key: string): Promise<PlaceholderState> {
  try {
    const stored = await chrome.storage.local.get(key);
    const state = stored[key] as PlaceholderState | undefined;
    if (!state || Date.now() - state.updatedAt > STATE_TTL_MS) return emptyState();
    return state;
  } catch {
    return emptyState();
  }
}

export async function saveState(key: string, state: PlaceholderState): Promise<void> {
  try {
    await chrome.storage.local.set({ [key]: state });
  } catch (error) {
    console.warn('[tw-pii-filter] 配號狀態寫入失敗', error);
  }
}
