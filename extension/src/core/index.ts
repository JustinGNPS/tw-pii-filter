/**
 * 偵測核心（TypeScript 版）統一匯出，並提供 detectAll() 一次執行全部規則。
 *
 * 對應 Python 版 `core/rules/__init__.py`；兩版必須對同一段輸入產生
 * 完全相同的輸出，由 `tests/parity.test.ts` 把關。
 */

import { detectApiKey, isValidApiKey } from './api_key';
import { renumberReplacements, resolveOverlaps } from './conflict_resolver';
import { detectCreditCard, isValidCreditCard } from './credit_card';
import { detectEmail, isValidEmail } from './email';
import { detectTwId, isValidTwId } from './tw_id';
import { detectTwNhi, isValidTwNhi } from './tw_nhi';
import { detectTwPhoneL, isValidTwPhoneL } from './tw_phone_l';
import { detectTwPhoneM, isValidTwPhoneM } from './tw_phone_m';
import { detectTwTax, isValidTwTax } from './tw_tax';
import type { DetectionResult, Detector, Span } from './types';

export * from './types';
export {
  detectApiKey,
  isValidApiKey,
  detectCreditCard,
  isValidCreditCard,
  detectEmail,
  isValidEmail,
  detectTwId,
  isValidTwId,
  detectTwNhi,
  isValidTwNhi,
  detectTwPhoneL,
  isValidTwPhoneL,
  detectTwPhoneM,
  isValidTwPhoneM,
  detectTwTax,
  isValidTwTax,
  resolveOverlaps,
  renumberReplacements,
};

/**
 * 依序執行的偵測器清單，新增規則時同步加進來即可被 detectAll() 涵蓋。
 *
 * ⚠️ 順序必須與 Python 版 `core/rules/__init__.py` 的 `_DETECTORS` 一致——
 * Layer 4 仲裁在完全平手時依賴排序穩定性，順序不同會導致兩版結果分歧。
 */
const DETECTORS: Detector[] = [
  detectTwId,
  detectTwTax,
  detectTwNhi,
  detectTwPhoneM,
  detectTwPhoneL,
  detectEmail,
  detectCreditCard,
  detectApiKey,
];

/**
 * 依序執行所有規則（source="rule"），並可透過 extraSpans 帶入語意層
 * （如 D 的 NER model，source="model"）已產生的 spans 一併整合。
 *
 * 所有 spans 合併後經 Layer 4 解析重疊衝突，回傳互不重疊的單一結果，
 * 符合 docs/interface.md 的約定。
 *
 * @param text       待偵測的原始文字
 * @param extraSpans 語意層（或其他外部來源）已產生、符合介面格式的 spans
 */
export function detectAll(text: string, extraSpans?: Span[] | null): DetectionResult {
  let spans: Span[] = [];
  for (const detector of DETECTORS) {
    spans.push(...detector(text).spans);
  }

  if (extraSpans && extraSpans.length > 0) {
    spans.push(...extraSpans);
  }

  spans = resolveOverlaps(spans);
  spans = renumberReplacements(spans);

  return { text, spans };
}
