/**
 * 電子郵件地址（EMAIL）格式驗證與偵測。
 * 移植自 Python 版 `core/rules/email.py`，行為必須完全一致。
 */

import type { DetectionResult, Span } from './types';
import { anchored, findIter, makeRuleSpan } from './util';

/**
 * 標準 email 格式：local-part @ domain.tld
 * local-part 允許英數字與常見符號 . _ % + -；domain 允許英數字、. 與 -；tld 至少 2 碼英文字母。
 */
const EMAIL_SOURCE =
  '(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}(?![A-Za-z0-9._%+-])';

const EMAIL_PATTERN = new RegExp(EMAIL_SOURCE, 'g');

/** 對應 Python 的 `_EMAIL_PATTERN.fullmatch(...)`。 */
const EMAIL_FULLMATCH = anchored(EMAIL_SOURCE);

/** 驗證字串是否為合法 email 格式。 */
export function isValidEmail(emailStr: unknown): boolean {
  if (typeof emailStr !== 'string') return false;
  return EMAIL_FULLMATCH.test(emailStr.trim());
}

/** 在文字中找出所有 email 地址。 */
export function detectEmail(text: string): DetectionResult {
  const spans: Span[] = [];
  let count = 0;
  for (const match of findIter(EMAIL_PATTERN, text)) {
    const candidate = match[0];
    count += 1;
    spans.push(
      makeRuleSpan(match.index, match.index + candidate.length, 'EMAIL', candidate, 0.95, count),
    );
  }
  return { text, spans };
}
