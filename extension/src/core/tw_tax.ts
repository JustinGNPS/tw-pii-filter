/**
 * 統一編號（TW_TAX）checksum 驗證與偵測。
 * 移植自 Python 版 `core/rules/tw_tax.py`，行為必須完全一致。
 */

import type { DetectionResult, Span } from './types';
import { findIter, isAsciiDigits, makeRuleSpan } from './util';

const WEIGHTS = [1, 2, 1, 2, 1, 2, 4, 1];

/** 候選字串：連續 8 碼數字，前後不可緊接英數字，避免截斷更長的字串。 */
const TW_TAX_PATTERN = /(?<![A-Za-z0-9])[0-9]{8}(?![A-Za-z0-9])/g;

/** 乘積若為兩位數，個位與十位相加。 */
function digitSum(product: number): number {
  return product >= 10 ? Math.floor(product / 10) + (product % 10) : product;
}

/** 驗證統一編號的檢查碼是否正確。 */
export function isValidTwTax(taxStr: unknown): boolean {
  if (typeof taxStr !== 'string') return false;

  const candidate = taxStr.trim();
  if (candidate.length !== 8 || !isAsciiDigits(candidate)) return false;

  const digits = [...candidate].map(Number);

  // 第 7 碼（index 6）為 7 時單獨處理，其餘各位照一般規則相加
  const baseSum = digits.reduce(
    (sum, digit, i) => (i === 6 ? sum : sum + digitSum(digit * WEIGHTS[i])),
    0,
  );

  if (digits[6] === 7) {
    // 4*7=28 相加為 10，非個位數，特例規定該位可算 0 或 1，
    // 兩種情況只要有一種能被 5 整除即為有效
    return [0, 1].some((alt) => (baseSum + alt) % 5 === 0);
  }

  return (baseSum + digitSum(digits[6] * WEIGHTS[6])) % 5 === 0;
}

/** 在文字中找出所有 checksum 正確的統一編號。 */
export function detectTwTax(text: string): DetectionResult {
  const spans: Span[] = [];
  let count = 0;
  for (const match of findIter(TW_TAX_PATTERN, text)) {
    const candidate = match[0];
    if (!isValidTwTax(candidate)) continue;
    count += 1;
    spans.push(
      makeRuleSpan(match.index, match.index + candidate.length, 'TW_TAX', candidate, 0.95, count),
    );
  }
  return { text, spans };
}
