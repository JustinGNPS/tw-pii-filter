/**
 * 佔位符配號測試。
 *
 * 重點是重現 B 在 PR #3 提出的問題：`detect_all()` 每次從 1 重新編號，
 * 直接拿 `span.replacement` 當佔位符，跨次呼叫會讓**同一個號碼對到不同真值**。
 * 還原時就會把兩個人的個資對調——比洩漏更嚴重。
 */

import { describe, expect, it } from 'vitest';

import { detectAll } from '../src/core';
import { maskText } from '../src/masking';
import { PlaceholderAllocator } from '../src/placeholder';

/** 兩個 checksum 正確的身分證，只是在兩段文字裡出現順序相反。 */
const ID_A = 'A123456789';
const ID_B = 'F131104093';

const TEXT_1 = `甲方 ${ID_A}，乙方 ${ID_B}`;
const TEXT_2 = `乙方 ${ID_B}，甲方 ${ID_A}`;

describe('detect_all 的 replacement 不能直接當佔位符', () => {
  it('重現問題：同一個號碼在兩次偵測中對到不同的真值', () => {
    const first = detectAll(TEXT_1).spans;
    const second = detectAll(TEXT_2).spans;

    // 兩次都是從 1 開始編號
    expect(first.map((s) => s.replacement)).toEqual(['[TW_ID_1]', '[TW_ID_2]']);
    expect(second.map((s) => s.replacement)).toEqual(['[TW_ID_1]', '[TW_ID_2]']);

    // 但 [TW_ID_1] 第一次指 ID_A、第二次指 ID_B —— 這就是會對調個資的原因
    expect(first[0].text).toBe(ID_A);
    expect(second[0].text).toBe(ID_B);
    expect(first[0].replacement).toBe(second[0].replacement);
    expect(first[0].text).not.toBe(second[0].text);
  });
});

describe('PlaceholderAllocator', () => {
  it('同一個真值跨次呼叫永遠拿到同一個佔位符', () => {
    const allocator = new PlaceholderAllocator();

    const first = maskText(TEXT_1, detectAll(TEXT_1).spans, allocator);
    const second = maskText(TEXT_2, detectAll(TEXT_2).spans, allocator);

    const placeholderOf = (result: typeof first, original: string) =>
      result.mapping.find((entry) => entry.original === original)?.placeholder;

    expect(placeholderOf(first, ID_A)).toBe(placeholderOf(second, ID_A));
    expect(placeholderOf(first, ID_B)).toBe(placeholderOf(second, ID_B));
    expect(placeholderOf(first, ID_A)).not.toBe(placeholderOf(first, ID_B));
  });

  it('不同真值永遠不會共用同一個佔位符', () => {
    const allocator = new PlaceholderAllocator();
    const seen = new Map<string, string>();

    for (const text of [TEXT_1, TEXT_2, `${ID_A} 又出現`, `新的一段 ${ID_B}`]) {
      const { mapping } = maskText(text, detectAll(text).spans, allocator);
      for (const entry of mapping) {
        const previous = seen.get(entry.placeholder);
        if (previous !== undefined) {
          expect(previous).toBe(entry.original);
        }
        seen.set(entry.placeholder, entry.original);
      }
    }
  });

  it('同一段文字裡重複出現的相同真值共用一個佔位符', () => {
    const text = `${ID_A} 稍後再次提到 ${ID_A}`;
    const { maskedText, mapping } = maskText(text, detectAll(text).spans);

    expect(mapping).toHaveLength(1);
    const placeholder = mapping[0].placeholder;
    expect(maskedText).toBe(`${placeholder} 稍後再次提到 ${placeholder}`);
  });

  it('不同類別碰巧有相同字串時分開配號', () => {
    const allocator = new PlaceholderAllocator();
    expect(allocator.allocate('TW_ID', '12345678')).toBe('[TW_ID_1]');
    expect(allocator.allocate('TW_TAX', '12345678')).toBe('[TW_TAX_1]');
    expect(allocator.size).toBe(2);
  });

  it('配號狀態可序列化、還原後延續同一套編號', () => {
    const first = new PlaceholderAllocator();
    first.allocate('TW_ID', ID_A);
    first.allocate('TW_ID', ID_B);

    const restored = new PlaceholderAllocator(first.toState());
    expect(restored.allocate('TW_ID', ID_A)).toBe('[TW_ID_1]');
    expect(restored.allocate('TW_ID', ID_B)).toBe('[TW_ID_2]');
    // 新的真值接著往下編，不會撞號
    expect(restored.allocate('TW_ID', 'H224567891')).toBe('[TW_ID_3]');
  });

  it('遮蔽後的文字裡不再出現任何原文', () => {
    const text = `病患 ${ID_A}，手機 0912345678，信箱 a@b.com`;
    const { maskedText } = maskText(text, detectAll(text).spans);

    expect(maskedText).not.toContain(ID_A);
    expect(maskedText).not.toContain('0912345678');
    expect(maskedText).not.toContain('a@b.com');
  });
});

describe('語意層類別代碼（Layer 2 尚未定案）', () => {
  it('未知代碼不會讓遮蔽壞掉，照樣配號與替換', () => {
    const text = '王小明住在台北市';
    const spans = [
      {
        start: 0,
        end: 3,
        type: 'name',
        text: '王小明',
        confidence: 0.98,
        source: 'model' as const,
        replacement: '[name_1]',
      },
      {
        start: 5,
        end: 8,
        type: 'address',
        text: '台北市',
        confidence: 0.91,
        source: 'model' as const,
        replacement: '[address_1]',
      },
    ];

    const { maskedText, mapping } = maskText(text, spans);
    expect(maskedText).toBe('[name_1]住在[address_1]');
    expect(mapping.map((entry) => entry.type)).toEqual(['name', 'address']);
  });
});
