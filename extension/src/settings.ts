/**
 * 使用者設定：要攔截哪些類型的個資。
 *
 * 預設全部攔截。使用者在 popup 取消勾選的類型，貼上時直接當作沒偵測到 ——
 * 不列進確認面板；若只剩這些類型，就跟「沒有個資」一樣直接貼上、不打擾。
 *
 * 只列規則層的 8 種：擴充目前沒有語意層，列出姓名、地址反而會讓使用者以為
 * 勾了就會攔。不在清單裡的類型（例如未來接上語意層後的 NAME）一律視為開啟，
 * 不會因為設定檔是舊的就被默默關掉。
 *
 * 與 proxy 的 `SKIP_TYPES` 不同：proxy 跳過的型別仍計入組合風險；這裡關掉的
 * 類型同樣不影響 `combination_risk`（那是 detectAll 算好的，這裡不動它）。
 */

import type { Span } from './core';
import { typeLabel } from './masking';

export const CONFIGURABLE_TYPES = [
  'TW_ID',
  'TW_TAX',
  'TW_NHI',
  'TW_PHONE_M',
  'TW_PHONE_L',
  'EMAIL',
  'CREDIT_CARD',
  'API_KEY',
] as const;

export const ENABLED_TYPES_KEY = 'enabledTypes';

export function configurableTypes(): Array<{ code: string; label: string }> {
  return CONFIGURABLE_TYPES.map((code) => ({ code, label: typeLabel(code) }));
}

/** 只濾掉「在可設定清單裡、且被使用者關掉」的類型。 */
export function filterEnabled(spans: Span[], enabled: ReadonlySet<string>): Span[] {
  const configurable = new Set<string>(CONFIGURABLE_TYPES);
  return spans.filter((span) => !configurable.has(span.type) || enabled.has(span.type));
}

export async function loadEnabledTypes(): Promise<Set<string>> {
  try {
    const stored = await chrome.storage.local.get(ENABLED_TYPES_KEY);
    const value = stored[ENABLED_TYPES_KEY];
    if (Array.isArray(value)) return new Set(value.filter((v): v is string => typeof v === 'string'));
  } catch {
    /* 讀不到就用預設 */
  }
  return new Set(CONFIGURABLE_TYPES);
}

export async function saveEnabledTypes(enabled: Iterable<string>): Promise<void> {
  try {
    await chrome.storage.local.set({ [ENABLED_TYPES_KEY]: [...enabled] });
  } catch (error) {
    console.warn('[tw-pii-filter] 設定寫入失敗', error);
  }
}
