/**
 * 台灣市內電話（TW_PHONE_L）格式驗證與偵測。
 * 移植自 Python 版 `core/rules/tw_phone_l.py`，行為必須完全一致。
 */

import type { DetectionResult, Span } from './types';
import { findIter, isAsciiDigits, makeRuleSpan } from './util';

/**
 * 候選字串：區碼（可用括號包住，2-4 碼，第二碼須為 2-8，避免與手機 09 開頭混淆）
 * + 分隔符（連字號或空白，可省略）+ 6-8 碼號碼（中間可有一個連字號）
 * 前後不可緊接數字，避免截斷更長的數字串。
 */
const TW_PHONE_L_PATTERN =
  /(?<![0-9])\(?0[2-8][0-9]{0,2}\)?[-\s]?[0-9]{3,4}-?[0-9]{3,4}(?![0-9])/g;

const STRIP_CHARS = /[()\-\s]/g;

/** 驗證字串是否為合法台灣市話格式（區碼 + 號碼，共 8-10 碼數字）。 */
export function isValidTwPhoneL(phoneStr: unknown): boolean {
  if (typeof phoneStr !== 'string') return false;

  const digits = phoneStr.trim().replace(STRIP_CHARS, '');
  if (!isAsciiDigits(digits) || digits.length < 8 || digits.length > 10) return false;

  // 區碼須為 0 開頭，第二碼 2-8（09 開頭為手機，非市話區碼）
  return digits[0] === '0' && '2345678'.includes(digits[1]);
}

/** 在文字中找出所有台灣市話號碼。 */
export function detectTwPhoneL(text: string): DetectionResult {
  const spans: Span[] = [];
  let count = 0;
  for (const match of findIter(TW_PHONE_L_PATTERN, text)) {
    const candidate = match[0];
    if (!isValidTwPhoneL(candidate)) continue;
    count += 1;
    spans.push(
      makeRuleSpan(
        match.index,
        match.index + candidate.length,
        'TW_PHONE_L',
        candidate,
        0.85,
        count,
      ),
    );
  }
  return { text, spans };
}
