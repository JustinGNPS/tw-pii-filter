/**
 * 台灣手機號碼（TW_PHONE_M）格式驗證與偵測。
 * 移植自 Python 版 `core/rules/tw_phone_m.py`，行為必須完全一致。
 */

import type { DetectionResult, Span } from './types';
import { findIter, isAsciiDigits, makeRuleSpan } from './util';

/**
 * 候選字串：09 開頭 + 8 碼數字，中間可有 0~2 個連字號（如 0912-345-678、0912345678）
 * 前後不可緊接數字，避免截斷更長的數字串。
 */
const TW_PHONE_M_PATTERN = /(?<![0-9])09[0-9]{2}-?[0-9]{3}-?[0-9]{3}(?![0-9])/g;

/** 驗證字串是否為合法台灣手機號碼格式（09 開頭共 10 碼數字）。 */
export function isValidTwPhoneM(phoneStr: unknown): boolean {
  if (typeof phoneStr !== 'string') return false;

  const digits = phoneStr.trim().replaceAll('-', '');
  if (digits.length !== 10 || !isAsciiDigits(digits)) return false;

  return digits.startsWith('09');
}

/** 在文字中找出所有台灣手機號碼。 */
export function detectTwPhoneM(text: string): DetectionResult {
  const spans: Span[] = [];
  let count = 0;
  for (const match of findIter(TW_PHONE_M_PATTERN, text)) {
    const candidate = match[0];
    if (!isValidTwPhoneM(candidate)) continue;
    count += 1;
    spans.push(
      makeRuleSpan(
        match.index,
        match.index + candidate.length,
        'TW_PHONE_M',
        candidate,
        0.9,
        count,
      ),
    );
  }
  return { text, spans };
}
