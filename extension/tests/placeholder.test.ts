/**
 * 佔位符配號測試。
 *
 * 重點是重現 B 在 PR #3 提出的問題：`detect_all()` 每次從 1 重新編號，
 * 直接拿 `span.replacement` 當佔位符，跨次呼叫會讓**同一個號碼對到不同真值**。
 * 還原時就會把兩個人的個資對調——比洩漏更嚴重。
 */

import { describe, expect, it } from 'vitest';

import { detectAll } from '../src/core';
import { isKnownType, isNonPii, maskText, typeLabel } from '../src/masking';
import { FALLBACK_TYPE, PlaceholderAllocator, normalizeType } from '../src/placeholder';

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

describe('語意層類別代碼', () => {
  const nerSpans = [
    {
      start: 0,
      end: 3,
      type: 'NAME',
      text: '王小明',
      confidence: 0.98,
      source: 'model' as const,
      replacement: '[NAME_1]',
    },
    {
      start: 5,
      end: 8,
      type: 'ADDRESS',
      text: '台北市',
      confidence: 0.91,
      source: 'model' as const,
      replacement: '[ADDRESS_1]',
    },
  ];

  it('語意層的 spans 照樣配號與替換', () => {
    const { maskedText, mapping } = maskText('王小明住在台北市', nerSpans);
    expect(maskedText).toBe('[NAME_1]住在[ADDRESS_1]');
    expect(mapping.map((entry) => entry.type)).toEqual(['NAME', 'ADDRESS']);
  });

  it('模型定義的 14 種型別全部都有登記顯示名稱', () => {
    const allModelTypes = [
      'NAME', 'ADDRESS', 'COMPANY', 'ORGANIZATION', 'GOVERNMENT', 'POSITION',
      'EMAIL', 'MOBILE', 'QQ', 'VX', 'BOOK', 'GAME', 'MOVIE', 'SCENE',
    ];
    for (const type of allModelTypes) {
      expect(isKnownType(type), `${type} 沒有登記顯示名稱`).toBe(true);
      expect(typeLabel(type)).not.toBe(type); // 有中文名稱，不是直接回傳代碼
    }
  });

  it('非個資類別（書名/遊戲/影劇/景點）被正確標記', () => {
    for (const type of ['BOOK', 'GAME', 'MOVIE', 'SCENE']) {
      expect(isNonPii(type), `${type} 應視為非個資`).toBe(true);
    }
    for (const type of ['NAME', 'ADDRESS', 'TW_ID', 'POSITION', 'COMPANY']) {
      expect(isNonPii(type), `${type} 不該被視為非個資`).toBe(false);
    }
  });

  it('未登記的代碼不會讓遮蔽壞掉', () => {
    const text = '某個新類別';
    const { maskedText } = maskText(text, [
      {
        start: 0,
        end: 2,
        type: 'BRAND_NEW_TYPE',
        text: '某個',
        confidence: 0.8,
        source: 'model' as const,
        replacement: '[BRAND_NEW_TYPE_1]',
      },
    ]);
    expect(maskedText).toBe('[BRAND_NEW_TYPE_1]新類別');
  });
});

describe('normalizeType（與 proxy 的 mapping.normalize_type 對應）', () => {
  it('小寫代碼轉成大寫，避免產生 proxy 還原不了的佔位符', () => {
    expect(normalizeType('name')).toBe('NAME');
    expect(normalizeType('address')).toBe('ADDRESS');
  });

  it('規則層既有的大寫代碼維持原樣（底線不可被吃掉）', () => {
    for (const type of ['TW_ID', 'TW_TAX', 'TW_NHI', 'TW_PHONE_M', 'TW_PHONE_L', 'EMAIL', 'CREDIT_CARD', 'API_KEY']) {
      expect(normalizeType(type)).toBe(type);
    }
  });

  it('型別裡的數字會被清掉（佔位符用底線分隔型別與序號，型別含數字會無法反解）', () => {
    expect(normalizeType('ADDRESS2')).toBe('ADDRESS');
  });

  it('空值或無法正規化的輸入退回 PII', () => {
    expect(normalizeType('')).toBe(FALLBACK_TYPE);
    expect(normalizeType(null)).toBe(FALLBACK_TYPE);
    expect(normalizeType(undefined)).toBe(FALLBACK_TYPE);
    expect(normalizeType('123')).toBe(FALLBACK_TYPE);
  });

  it('大小寫不同的同一型別共用計數器，不會產生兩個 [NAME_1] 指向不同的人', () => {
    const allocator = new PlaceholderAllocator();
    expect(allocator.allocate('name', '王小明')).toBe('[NAME_1]');
    expect(allocator.allocate('NAME', '陳大同')).toBe('[NAME_2]');
    // 同一個真值不論用哪種寫法傳進來，都拿到同一個佔位符
    expect(allocator.allocate('NAME', '王小明')).toBe('[NAME_1]');
    expect(allocator.allocate('name', '王小明')).toBe('[NAME_1]');
  });
});
