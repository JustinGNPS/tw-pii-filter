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
    validateExtraSpans(extraSpans);
    spans.push(...extraSpans);
  }

  spans = resolveOverlaps(spans);
  spans = renumberReplacements(spans);

  // Layer 3（組合風險評分，見 docs/interface.md「組合風險評分」一節）尚未
  // 移植到 TypeScript 版：Python 版的 compute_combination_risk() 需要
  // ADDRESS/POSITION 這類準識別子 span（來自語意層/NER，擴充目前沒有）
  // 與 AGE/GENDER 的獨立正則掃描（TS 版尚無對應實作）。型別先對齊
  // Python 版的契約（見 CombinationRisk），實際計算邏輯留待移植
  // core/risk/combination_risk.py 時一併補上——因此一律回傳 null，
  // 而不是省略這個欄位，避免下游誤把「沒有欄位」跟「沒有風險」混為一談。
  return { text, spans, combination_risk: null };
}

/**
 * 檢查語意層傳進來的 spans 具備仲裁所需的欄位。
 *
 * B 在 PR #3 指出 Python 版 `conflict_resolver.py` 直接索引 `span["confidence"]`，
 * 語意層若沒帶 confidence 會 KeyError。TypeScript 這邊的情況**更糟**：
 * `-span.confidence` 對 undefined 會得到 `NaN`，排序比較全部回傳 NaN，
 * 仲裁結果變成未定義行為卻不會拋錯——錯得無聲無息。
 *
 * 所以這裡主動檢查並拋出明確錯誤，讓兩版都是「大聲失敗」而不是靜默出錯。
 */
function validateExtraSpans(extraSpans: Span[]): void {
  extraSpans.forEach((span, index) => {
    if (typeof span.confidence !== 'number' || Number.isNaN(span.confidence)) {
      throw new TypeError(
        `extraSpans[${index}] 缺少有效的 confidence（語意層的 span 必須帶 confidence 與 source，` +
          `否則 Layer 4 仲裁無法排序）：${JSON.stringify(span)}`,
      );
    }
    if (span.source !== 'rule' && span.source !== 'model') {
      throw new TypeError(
        `extraSpans[${index}] 的 source 必須是 "rule" 或 "model"，收到：${String(span.source)}`,
      );
    }
    if (
      typeof span.start !== 'number' ||
      typeof span.end !== 'number' ||
      span.start >= span.end
    ) {
      throw new TypeError(
        `extraSpans[${index}] 的 start/end 無效（需為字元索引且 start < end）：${JSON.stringify(span)}`,
      );
    }
  });
}
