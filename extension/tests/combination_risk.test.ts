/**
 * detectAll() 的 combination_risk 欄位（Layer 3，issue #20）。
 *
 * TypeScript 版目前尚未移植 `core/risk/combination_risk.py` 的計算邏輯
 * （見 src/core/index.ts 的說明：需要語意層 span 與 AGE/GENDER 正則，
 * 兩者 TS 版都還沒有）。這裡把「一律回傳 null」的行為釘住，一方面確認
 * 欄位確實存在（不是被省略），一方面讓之後真的補上計算邏輯時，
 * 這個測試會理所當然地失敗、提醒維護者一併更新這份說明。
 */

import { describe, expect, it } from 'vitest';

import { detectAll } from '../src/core';

describe('detectAll 的 combination_risk 欄位', () => {
  it('欄位存在且值為 null（尚未移植 Layer 3 計算邏輯）', () => {
    const result = detectAll('這是一段普通文字，沒有任何個資。');
    expect(result).toHaveProperty('combination_risk');
    expect(result.combination_risk).toBeNull();
  });

  it('即使文字本身帶有年齡等準識別子語意，TS 版目前仍不計算（已知限制）', () => {
    // 對應 Python 版會算出風險的例子（見 tests/test_rules_init.py），
    // TS 版目前沒有 AGE 正則也沒有語意層 span，仍應回傳 null。
    const result = detectAll('他32歲，住在新竹，是一名工程師。');
    expect(result.combination_risk).toBeNull();
  });
});
