/**
 * Layer 4：spans 重疊衝突解析。
 * 移植自 Python 版 `core/rules/conflict_resolver.py`，行為必須完全一致。
 *
 * 多個規則各自偵測同一段文字時，可能產生重疊的 span（例如手機號碼剛好是
 * email local-part 的一部分）。本模組負責在合併後的 span 清單中，針對重疊
 * 片段依優先順序保留其中一個、移除其餘的，確保輸出的 spans 互不重疊，讓
 * 下游可以安全地依座標（start/end）直接替換文字。
 *
 * 優先順序：
 * 1. 範圍（end - start）較大者優先
 * 2. 範圍相同時，confidence 較高者優先
 * 3. 以上皆相同時，source === "rule" 優先於 source === "model"
 */

import type { PiiType, Span } from './types';

function priorityKey(span: Span): [number, number, number] {
  const length = span.end - span.start;
  const sourceRank = span.source === 'rule' ? 0 : 1;
  return [-length, -span.confidence, sourceRank];
}

function overlaps(a: Span, b: Span): boolean {
  return a.start < b.end && b.start < a.end;
}

/**
 * 依優先順序解析重疊的 span，回傳互不重疊的 span 清單（依 start 升序排列）。
 *
 * 採貪婪演算法：先依優先順序排序所有候選 span，再依序嘗試保留，
 * 只要與目前已保留的任何 span 重疊就捨棄，藉此確保結果彼此互不重疊。
 */
export function resolveOverlaps(spans: Span[]): Span[] {
  const sorted = [...spans].sort((a, b) => {
    const ka = priorityKey(a);
    const kb = priorityKey(b);
    return ka[0] - kb[0] || ka[1] - kb[1] || ka[2] - kb[2];
  });

  const accepted: Span[] = [];
  for (const span of sorted) {
    if (!accepted.some((kept) => overlaps(span, kept))) {
      accepted.push(span);
    }
  }

  accepted.sort((a, b) => a.start - b.start || a.end - b.end);
  return accepted;
}

/**
 * 在 spans 已經互不重疊、依 start 排序後，依 type 分組重新編號 replacement 欄位。
 *
 * 衝突解析可能移除掉某個 type 較早出現的 span，使原本各偵測器獨立編出的
 * 序號留下缺口（例如只剩 TW_ID_2 卻沒有 TW_ID_1）。本函式在仲裁完成後
 * 重新依序編號，確保每個 type 的序號從 1 開始連續遞增、不跳號。
 */
export function renumberReplacements(spans: Span[]): Span[] {
  const counters = new Map<PiiType, number>();
  for (const span of spans) {
    const next = (counters.get(span.type) ?? 0) + 1;
    counters.set(span.type, next);
    span.replacement = `[${span.type}_${next}]`;
  }
  return spans;
}
