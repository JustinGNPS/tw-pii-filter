/**
 * 台灣身分證字號（TW_ID）checksum 驗證與偵測。
 * 移植自 Python 版 `core/rules/tw_id.py`，行為必須完全一致。
 */

import type { DetectionResult, Span } from './types';
import { findIter, isAsciiDigits, makeRuleSpan } from './util';

/** 首字母對應兩位數字（標準對照表）。 */
const LETTER_MAP: Record<string, number> = {
  A: 10, B: 11, C: 12, D: 13, E: 14, F: 15, G: 16, H: 17,
  I: 34, J: 18, K: 19, L: 20, M: 21, N: 22, O: 35, P: 23,
  Q: 24, R: 25, S: 26, T: 27, U: 28, V: 29, W: 32, X: 30,
  Y: 31, Z: 33,
};

/** 依序對應 [n1, n2, d1..d9] 共 11 位的權重。 */
const WEIGHTS = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1];

/** 候選字串：1 個英文字母 + 9 碼數字，前後不可緊接英數字，避免截斷更長的字串。 */
const TW_ID_PATTERN = /(?<![A-Za-z0-9])[A-Za-z][0-9]{9}(?![A-Za-z0-9])/g;

/** 驗證台灣身分證字號的檢查碼是否正確。 */
export function isValidTwId(idStr: unknown): boolean {
  if (typeof idStr !== 'string') return false;

  const candidate = idStr.trim().toUpperCase();
  if (candidate.length !== 10) return false;

  const letter = candidate[0];
  const digits = candidate.slice(1);
  if (!(letter in LETTER_MAP) || !isAsciiDigits(digits)) return false;

  const mapped = LETTER_MAP[letter];
  const values = [Math.floor(mapped / 10), mapped % 10, ...[...digits].map(Number)];

  const total = values.reduce((sum, value, i) => sum + value * WEIGHTS[i], 0);
  return total % 10 === 0;
}

/** 在文字中找出所有 checksum 正確的台灣身分證字號。 */
export function detectTwId(text: string): DetectionResult {
  const spans: Span[] = [];
  let count = 0;
  for (const match of findIter(TW_ID_PATTERN, text)) {
    const candidate = match[0];
    if (!isValidTwId(candidate)) continue;
    count += 1;
    spans.push(
      makeRuleSpan(match.index, match.index + candidate.length, 'TW_ID', candidate, 0.99, count),
    );
  }
  return { text, spans };
}
