/**
 * 已知的 Python ↔ TypeScript 行為分歧。
 *
 * ## 目前：沒有已知分歧 ✅
 *
 * 這個檔案原本釘的是**全形數字**的分歧：Python 的 `\d` 是 Unicode-aware
 * 會匹配全形，JavaScript 的只認 ASCII，而 Python 自己也不一致
 * （全形 TW_TAX / TW_NHI 抓得到，全形 TW_ID / TW_PHONE_M 抓不到）。
 *
 * 這個分歧**已於 issue #21 / #27 修復**：兩版都在偵測前做全形→半形正規化
 * （`core/rules/normalize.py` 與 `extension/src/core/normalize.ts`），
 * 四種型別現在全部抓得到，兩版行為一致。原本的分歧語料已移入
 * `tests/fixtures/parity_cases.json`，改由正式的一致性測試把關。
 *
 * ## 這個檔案為什麼留著
 *
 * 分歧語料的機制（`divergence_cases.json` + `divergence_python.json` +
 * 本測試）本身是有價值的：下次再發現 Python / JS 行為不同、而且一時無法
 * 或不該立刻修時，把語料丟進 `divergence_cases.json` 就能把分歧「釘住」，
 * 讓它不會默默漂移。目前語料為空，測試只驗證「確實沒有已知分歧」。
 */

import { describe, expect, it } from 'vitest';

import { detectAll } from '../src/core';
import cases from '../../tests/fixtures/divergence_cases.json';
import pythonResults from '../../tests/fixtures/divergence_python.json';

describe('已知分歧清單', () => {
  it('目前沒有任何已知分歧（全形數字分歧已於 issue #21 修復）', () => {
    expect(cases).toEqual([]);
    expect(pythonResults).toEqual([]);
  });
});

describe('全形數字：修復後的行為（原本是分歧來源）', () => {
  it.each([
    ['全形統編', '統編 １２３４５６７５', 'TW_TAX'],
    ['全形身分證', '身分證 Ａ１２３４５６７８９', 'TW_ID'],
    ['全形手機', '手機 ０９１２３４５６７８', 'TW_PHONE_M'],
    ['全形信箱', '信箱 ａｂｃ@ｅｘａｍｐｌｅ.ｃｏｍ', 'EMAIL'],
  ])('%s 現在抓得到', (_label, text, expectedType) => {
    const { spans } = detectAll(text);
    expect(spans.map((span) => span.type)).toContain(expectedType);
  });

  it('span.text 保留使用者原本輸入的全形字元，不是被改寫過的半形版本', () => {
    const text = '統編 １２３４５６７５';
    const { spans } = detectAll(text);
    expect(spans).toHaveLength(1);
    expect(spans[0].text).toBe('１２３４５６７５');
    // docs/interface.md 的約定：text.slice(start, end) 必須等於 span.text
    expect(text.slice(spans[0].start, spans[0].end)).toBe(spans[0].text);
  });

  it('半形行為完全不受正規化影響', () => {
    const { spans } = detectAll('統編 12345675');
    expect(spans).toHaveLength(1);
    expect(spans[0].text).toBe('12345675');
  });

  it('全形與半形的同一個值視為不同原文（座標與內容都取自原文）', () => {
    const text = '全形 １２３４５６７５ 半形 12345675';
    const { spans } = detectAll(text);
    expect(spans.map((span) => span.text)).toEqual(['１２３４５６７５', '12345675']);
    for (const span of spans) {
      expect(text.slice(span.start, span.end)).toBe(span.text);
    }
  });
});
