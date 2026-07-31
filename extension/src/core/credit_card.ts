/**
 * 信用卡號（CREDIT_CARD）Luhn checksum 驗證與偵測。
 * 移植自 Python 版 `core/rules/credit_card.py`，行為必須完全一致。
 */

import type { DetectionResult, Span } from './types';
import { findIter, isAsciiDigits, makeRuleSpan } from './util';

/**
 * 候選字串：13-19 碼數字，中間可用空白或連字號每隔數碼分隔
 * 前後不可緊接英數字，避免截斷更長的字串。
 */
const CREDIT_CARD_PATTERN = /(?<![A-Za-z0-9])[0-9](?:[ -]?[0-9]){12,18}(?![A-Za-z0-9])/g;

const STRIP_CHARS = /[ -]/g;

/** 計算 Luhn 演算法的檢查總和，對合法卡號應為 0（mod 10）。 */
function luhnChecksum(digits: string): number {
  let total = 0;
  const reversed = [...digits].reverse();
  for (let i = 0; i < reversed.length; i += 1) {
    let value = Number(reversed[i]);
    if (i % 2 === 1) {
      value *= 2;
      if (value > 9) value -= 9;
    }
    total += value;
  }
  return total % 10;
}

/** 驗證字串是否為合法信用卡號（13-19 碼數字，且通過 Luhn checksum）。 */
export function isValidCreditCard(cardStr: unknown): boolean {
  if (typeof cardStr !== 'string') return false;

  const digits = cardStr.trim().replace(STRIP_CHARS, '');
  if (!isAsciiDigits(digits) || digits.length < 13 || digits.length > 19) return false;

  return luhnChecksum(digits) === 0;
}

/** 在文字中找出所有 checksum 正確的信用卡號。 */
export function detectCreditCard(text: string): DetectionResult {
  const spans: Span[] = [];
  let count = 0;
  for (const match of findIter(CREDIT_CARD_PATTERN, text)) {
    const candidate = match[0];
    if (!isValidCreditCard(candidate)) continue;
    count += 1;
    spans.push(
      makeRuleSpan(
        match.index,
        match.index + candidate.length,
        'CREDIT_CARD',
        candidate,
        0.95,
        count,
      ),
    );
  }
  return { text, spans };
}
