/**
 * 移植 Python 規則層時共用的小工具。
 *
 * ## Python 與 JavaScript 的行為差異（移植時務必知道）
 *
 * 1. **`\d` 的涵蓋範圍**：Python 的 `re` 對 `str` 套用 `\d` 時會匹配 Unicode 數字
 *    （含全形０-９、阿拉伯數字等）；JavaScript 的 `\d` 只匹配 ASCII `0-9`。
 *    本專案刻意採用 JavaScript 的 ASCII-only 語意——身分證、統編等格式在法律上
 *    就是 ASCII 數字，全形輸入應視為不匹配。差異已記錄於 `tests/parity.test.ts`。
 *
 * 2. **`str.isdigit()`**：Python 的 `isdigit()` 同樣涵蓋全形數字，這裡以
 *    {@link isAsciiDigits} 取代，維持 ASCII-only 語意。
 *
 * 3. **`re.fullmatch()`**：JavaScript 沒有對應方法，以 {@link anchored} 包成
 *    `^(?:...)$` 的形式模擬。
 */

import type { PiiType, Span } from './types';

/** 對應 Python `str.isdigit()`，但限定 ASCII 數字（見上方說明第 1、2 點）。 */
export function isAsciiDigits(value: string): boolean {
  return value.length > 0 && /^[0-9]+$/.test(value);
}

/**
 * 把一段 regex 來源字串包成完整匹配（對應 Python 的 `re.fullmatch`）。
 *
 * @param source regex 來源字串（`RegExp.prototype.source`）
 * @param flags  要保留的旗標，例如 `'i'`
 */
export function anchored(source: string, flags = ''): RegExp {
  return new RegExp(`^(?:${source})$`, flags);
}

/**
 * 對應 Python 的 `pattern.finditer(text)`：回傳所有非重疊匹配。
 *
 * 傳入的 pattern 必須帶 `g` 旗標。每次呼叫都會重置 `lastIndex`，
 * 避免共用 module-level RegExp 時殘留狀態造成漏抓（JS 的經典陷阱）。
 */
export function findIter(pattern: RegExp, text: string): RegExpExecArray[] {
  if (!pattern.global) {
    throw new Error(`findIter 需要帶 g 旗標的 RegExp：${pattern}`);
  }
  pattern.lastIndex = 0;
  return [...text.matchAll(pattern)] as RegExpExecArray[];
}

/** 建立一筆符合 docs/interface.md 格式的規則層 span。 */
export function makeRuleSpan(
  start: number,
  end: number,
  type: PiiType,
  text: string,
  confidence: number,
  index: number,
): Span {
  return {
    start,
    end,
    type,
    text,
    confidence,
    source: 'rule',
    replacement: `[${type}_${index}]`,
  };
}
