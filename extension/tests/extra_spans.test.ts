/**
 * 語意層 spans 併入 detectAll 的驗證。
 *
 * 背景：B 在 PR #3 指出 Python 版 `conflict_resolver.py` 直接索引
 * `span["confidence"]`，語意層若沒帶這個欄位會 KeyError。
 * TypeScript 這邊更危險——`-undefined` 是 `NaN`，排序比較全回傳 NaN，
 * 仲裁結果變成未定義行為卻不拋錯。因此改成主動驗證、大聲失敗。
 */

import { describe, expect, it } from 'vitest';

import { detectAll } from '../src/core';
import type { Span } from '../src/core';

const TEXT = '王小明的身分證是 A123456789';

function nerSpan(overrides: Partial<Span> = {}): Span {
  return {
    start: 0,
    end: 3,
    type: 'name',
    text: '王小明',
    confidence: 0.98,
    source: 'model',
    replacement: '[name_1]',
    ...overrides,
  };
}

describe('detectAll 併入語意層 spans', () => {
  it('規則層與語意層的結果會一起輸出並重新編號', () => {
    const { spans } = detectAll(TEXT, [nerSpan()]);

    expect(spans.map((span) => span.type)).toEqual(['name', 'TW_ID']);
    expect(spans.map((span) => span.replacement)).toEqual(['[name_1]', '[TW_ID_1]']);
    expect(spans.map((span) => span.source)).toEqual(['model', 'rule']);
  });

  it('重疊時規則層優先（範圍相同、confidence 相同的情況）', () => {
    // 語意層誤把身分證整段標成 name，與規則層的 TW_ID 完全重疊
    const overlapping = nerSpan({
      start: TEXT.indexOf('A123456789'),
      end: TEXT.indexOf('A123456789') + 10,
      type: 'name',
      text: 'A123456789',
      confidence: 0.99, // 與 TW_ID 的 0.99 相同，仲裁落到第三條：rule 優先
    });

    const { spans } = detectAll(TEXT, [overlapping]);
    expect(spans).toHaveLength(1);
    expect(spans[0].type).toBe('TW_ID');
    expect(spans[0].source).toBe('rule');
  });

  it('缺少 confidence 會拋出明確錯誤，而不是靜默產生 NaN 排序', () => {
    const broken = nerSpan();
    delete (broken as Partial<Span>).confidence;

    expect(() => detectAll(TEXT, [broken])).toThrow(/confidence/);
  });

  it('source 不是 rule / model 會拋錯', () => {
    expect(() => detectAll(TEXT, [nerSpan({ source: 'ner' as never })])).toThrow(/source/);
  });

  it('start / end 無效會拋錯', () => {
    expect(() => detectAll(TEXT, [nerSpan({ start: 5, end: 5 })])).toThrow(/start\/end/);
  });

  it('extraSpans 為空或未傳時行為與純規則層相同', () => {
    const base = detectAll(TEXT);
    expect(detectAll(TEXT, [])).toEqual(base);
    expect(detectAll(TEXT, null)).toEqual(base);
  });
});
