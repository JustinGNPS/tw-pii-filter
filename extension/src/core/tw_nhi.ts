/**
 * 健保卡號（TW_NHI）格式驗證與偵測（無 checksum，僅驗證格式）。
 * 移植自 Python 版 `core/rules/tw_nhi.py`，行為必須完全一致。
 */

import type { DetectionResult, Span } from './types';
import { findIter, isAsciiDigits, makeRuleSpan } from './util';

/** 候選字串：連續 12 碼數字，前後不可緊接英數字，避免截斷更長的字串。 */
const TW_NHI_PATTERN = /(?<![A-Za-z0-9])[0-9]{12}(?![A-Za-z0-9])/g;

/** 驗證字串是否為合法健保卡號格式（純 12 碼數字，不驗 checksum）。 */
export function isValidTwNhi(nhiStr: unknown): boolean {
  if (typeof nhiStr !== 'string') return false;
  const candidate = nhiStr.trim();
  return candidate.length === 12 && isAsciiDigits(candidate);
}

/** 在文字中找出所有健保卡號。 */
export function detectTwNhi(text: string): DetectionResult {
  const spans: Span[] = [];
  let count = 0;
  for (const match of findIter(TW_NHI_PATTERN, text)) {
    const candidate = match[0];
    if (!isValidTwNhi(candidate)) continue;
    count += 1;
    spans.push(
      makeRuleSpan(match.index, match.index + candidate.length, 'TW_NHI', candidate, 0.6, count),
    );
  }
  return { text, spans };
}
