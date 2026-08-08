/**
 * Python ↔ TypeScript 規則層一致性測試。
 *
 * 擴充在瀏覽器裡跑不了 Python，所以規則層有兩套實作：
 *   - `core/rules/*.py`        → 給 B 的 proxy 用
 *   - `extension/src/core/*.ts` → 給 C 的瀏覽器擴充用
 *
 * 兩版一旦分歧，同一段文字在擴充與 proxy 會得到不同的遮蔽結果——這是最難
 * 察覺、也最傷公信力的 bug。本測試用同一批語料（`tests/fixtures/parity_cases.json`）
 * 逐筆比對 TypeScript 的 detectAll() 與 Python 版產生的快照
 * （`tests/fixtures/parity_expected.json`，由 `tools/gen_parity_expected.py` 產生）。
 *
 * 任何一方改動邏輯後，都必須重跑：
 *     python tools/gen_parity_expected.py
 * 並確認本測試仍然通過。
 */

import { describe, expect, it } from 'vitest';

import { detectAll } from '../src/core';
import type { DetectionResult } from '../src/core';
import cases from '../../tests/fixtures/parity_cases.json';
import expected from '../../tests/fixtures/parity_expected.json';

describe('Python ↔ TypeScript 規則層一致性', () => {
  it('語料與基準快照筆數相同', () => {
    expect(expected.length).toBe(cases.length);
  });

  it.each(cases.map((text, i) => [i, text] as const))(
    'case %i 的偵測結果與 Python 版完全一致',
    (index, text) => {
      const actual = detectAll(text);
      expect(actual).toEqual(expected[index] as unknown as DetectionResult);
    },
  );

  it('每個 span 的座標都能正確切出原文（offset 正確性）', () => {
    for (const text of cases) {
      for (const span of detectAll(text).spans) {
        expect(text.slice(span.start, span.end)).toBe(span.text);
      }
    }
  });

  it('detectAll 回傳的 spans 互不重疊且依 start 升序', () => {
    for (const text of cases) {
      const { spans } = detectAll(text);
      for (let i = 1; i < spans.length; i += 1) {
        expect(spans[i].start).toBeGreaterThanOrEqual(spans[i - 1].end);
      }
    }
  });

  it('每個 type 的 replacement 序號從 1 開始連續不跳號', () => {
    for (const text of cases) {
      const seen = new Map<string, number>();
      for (const span of detectAll(text).spans) {
        const next = (seen.get(span.type) ?? 0) + 1;
        seen.set(span.type, next);
        expect(span.replacement).toBe(`[${span.type}_${next}]`);
      }
    }
  });
});
