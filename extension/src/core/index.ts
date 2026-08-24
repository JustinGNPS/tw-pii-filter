/**
 * 偵測核心（TypeScript 版）統一匯出，並提供 detectAll() 一次執行全部規則。
 *
 * 對應 Python 版 `core/rules/__init__.py`；兩版必須對同一段輸入產生
 * 完全相同的輸出，由 `tests/parity.test.ts` 把關。
 */

import { detectApiKey, isValidApiKey } from './api_key';
import {
  WARNING_THRESHOLD,
  computeCombinationRisk,
  extractAge,
  isWarningWorthy,
} from './combination_risk';
import { renumberReplacements, resolveOverlaps } from './conflict_resolver';
import { hasFullWidth, toHalfWidth } from './normalize';
import { detectCreditCard, isValidCreditCard } from './credit_card';
import { detectEmail, isValidEmail } from './email';
import { detectTwId, isValidTwId } from './tw_id';
import { detectTwNhi, isValidTwNhi } from './tw_nhi';
import { detectTwPhoneL, isValidTwPhoneL } from './tw_phone_l';
import { detectTwPhoneM, isValidTwPhoneM } from './tw_phone_m';
import { detectTwTax, isValidTwTax } from './tw_tax';
import { NER_ALLOW_TYPES } from './types';
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
  // Layer 3
  computeCombinationRisk,
  isWarningWorthy,
  extractAge,
  WARNING_THRESHOLD,
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
  // 全形英數在中文輸入環境很常見（注音全形模式、從 Word/PDF 複製），
  // 但規則層的正則是 [0-9]/[A-Za-z]，對不到全形。偵測前先正規化，
  // 否則使用者貼含全形統編的合約會顯示「未偵測到敏感資訊」（issue #21）。
  //
  // 正規化只做全形英數與全形空格的定點映射，字元數保證不變，
  // 因此下面算出來的 start/end 可以直接沿用到原文（見 normalize.ts）。
  const scanText = hasFullWidth(text) ? toHalfWidth(text) : text;

  let spans: Span[] = [];
  for (const detector of DETECTORS) {
    spans.push(...detector(scanText).spans);
  }

  if (extraSpans && extraSpans.length > 0) {
    validateExtraSpans(extraSpans);
    // 型別白名單掛在 Layer 4 仲裁的**上游**：不採信的型別從一開始就不存在，
    // 不進仲裁、不算進提示筆數、也不進 Layer 3 組合風險分數。
    // 這與「偵測到但預設不勾選」是兩個不同機制，見 types.ts 的 NER_ALLOW_TYPES。
    spans.push(...extraSpans.filter(isTrustedSpan));
  }

  spans = resolveOverlaps(spans);
  spans = renumberReplacements(spans);

  // span.text 一律取自**原文**，維持 docs/interface.md 的約定
  // `text.slice(start, end) === span.text`——使用者在面板上該看到自己打的
  // 全形原文，不是被我們改寫過的半形版本。
  if (scanText !== text) {
    for (const span of spans) {
      span.text = text.slice(span.start, span.end);
    }
  }

  // Layer 3：組合風險評分（見 docs/interface.md「組合風險評分」一節）。
  // 依契約，score 為 0（準識別子共現數 < 2）時整個欄位為 null，
  // 而不是回傳 score: 0 的空殼物件——下游只要檢查是否為 null 即可。
  // 用正規化後的文字算 Layer 3：AGE 正則同樣是 [0-9]，全形年齡才抓得到
  const risk = computeCombinationRisk(scanText, spans);
  const combination_risk = risk.score > 0 ? risk : null;

  return { text, spans, combination_risk };
}

/**
 * 語意層的這筆 span 是否值得採信。
 *
 * 只對 `source === "model"` 的 span 套用白名單——規則層的型別不受影響
 * （規則層有 checksum / 格式驗證，本來就可信）。
 */
function isTrustedSpan(span: Span): boolean {
  if (span.source !== 'model') return true;
  return NER_ALLOW_TYPES.has(span.type);
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
