/**
 * Layer 3 組合風險評分（TypeScript 版）。
 *
 * 這支測試原本釘的是「TS 版尚未移植、一律回傳 null」的行為，作者當時預期
 * 「之後真的補上計算邏輯時，這個測試會理所當然地失敗」。實際上它**不會失敗**
 * ——它舉的例子（`他32歲，住在新竹，是一名工程師。`）只有 AGE 一個準識別子，
 * 而單一準識別子依規格本來就算 0 分、回傳 null。也就是說那個測試通過與否
 * 跟 Layer 3 有沒有實作無關，是個假綠燈。
 *
 * 現在改成實際驗證計算結果。要點：
 *  - 組合風險的定義是「多個準識別子併在一起」，所以**至少要兩個**才算分
 *  - 面板要用的是 `contributing_types` 與 `suggestions`，不只是分數，
 *    因此這些欄位也要一起釘住
 */

import { describe, expect, it } from 'vitest';

import {
  WARNING_THRESHOLD,
  computeCombinationRisk,
  detectAll,
  extractAge,
  isWarningWorthy,
} from '../src/core';
import type { Span } from '../src/core';

/** 產生一筆語意層 span（Layer 3 只看 type，座標不影響計分）。 */
function modelSpan(type: string, text: string): Span {
  return {
    start: 0,
    end: text.length,
    type,
    text,
    confidence: 0.9,
    source: 'model',
    replacement: `[${type}_1]`,
  };
}

describe('detectAll 的 combination_risk 欄位', () => {
  it('沒有準識別子時為 null（不是 score: 0 的空殼物件）', () => {
    const result = detectAll('這是一段普通文字，沒有任何個資。');
    expect(result).toHaveProperty('combination_risk');
    expect(result.combination_risk).toBeNull();
  });

  it('只有單一準識別子時仍為 null——組合風險的定義是「多個併在一起」', () => {
    // 只有 AGE，沒有第二個準識別子（規則層不產生 ADDRESS / POSITION span）
    expect(detectAll('他32歲。').combination_risk).toBeNull();
    // 只有 GENDER
    expect(detectAll('這位先生沒有透露年齡').combination_risk).toBeNull();
  });

  it('AGE + GENDER 兩個準識別子共現時算得出分數', () => {
    const risk = detectAll('35歲男性，目前住在新竹').combination_risk;
    expect(risk).not.toBeNull();
    expect(risk!.contributing_types).toEqual(['AGE', 'GENDER']);
    expect(risk!.score).toBeCloseTo(0.5, 5); // 0.35 + 0.15
    expect(risk!.risk_level).toBe('中');
  });
});

describe('computeCombinationRisk', () => {
  it('語意層 span 的準識別子型別會計入', () => {
    const risk = computeCombinationRisk('某人的資料', [
      modelSpan('ADDRESS', '新竹市東區'),
      modelSpan('POSITION', '資深後端工程師'),
    ]);
    expect(risk.contributing_types).toEqual(['ADDRESS', 'POSITION']);
    expect(risk.score).toBeCloseTo(0.5, 5); // 0.30 + 0.20
  });

  it('非準識別子的型別不影響分數', () => {
    // NAME 是個資，但不是準識別子——它是直接識別子，不進組合風險計分
    const risk = computeCombinationRisk('某人的資料', [
      modelSpan('NAME', '王小明'),
      modelSpan('ADDRESS', '新竹市東區'),
    ]);
    expect(risk.contributing_types).toEqual(['ADDRESS']);
    expect(risk.score).toBe(0); // 只剩一個準識別子
  });

  it('分數封頂在 1.0', () => {
    const risk = computeCombinationRisk('40歲的女士', [
      modelSpan('ADDRESS', '新竹市東區'),
      modelSpan('POSITION', '工程師'),
      modelSpan('COMPANY', '某科技公司'),
      modelSpan('ORGANIZATION', '某協會'),
      modelSpan('GOVERNMENT', '某局'),
      modelSpan('SCENE', '某公園'),
    ]);
    // 0.35+0.15+0.30+0.20+0.15+0.15+0.15+0.10 = 1.55 → 封頂
    expect(risk.score).toBe(1.0);
    expect(risk.risk_level).toBe('高');
  });

  it('達門檻時 isWarningWorthy 為真', () => {
    const high = computeCombinationRisk('35歲男性', [
      modelSpan('ADDRESS', '新竹市'),
      modelSpan('POSITION', '工程師'),
    ]);
    expect(high.score).toBeGreaterThanOrEqual(WARNING_THRESHOLD);
    expect(isWarningWorthy(high)).toBe(true);
    expect(isWarningWorthy(null)).toBe(false);
  });

  it('每個貢獻型別都對應一則泛化建議（面板要顯示，不能只給分數）', () => {
    const risk = computeCombinationRisk('35歲男性', [modelSpan('ADDRESS', '新竹市')]);
    expect(risk.contributing_types).toEqual(['ADDRESS', 'AGE', 'GENDER']);
    expect(risk.suggestions).toHaveLength(3);
    // 年齡建議會帶入實際數字，才有可操作性
    expect(risk.suggestions.some((s) => s.includes('35歲'))).toBe(true);
  });
});

describe('年齡格式涵蓋範圍', () => {
  // 用固定日期，避免民國年/西元年推算的結果隨系統時間改變（見下方 describe）
  const TODAY = new Date('2026-08-25T00:00:00Z');

  it.each([
    ['阿拉伯數字', '他35歲', 35],
    ['中文數字（十位+個位）', '三十五歲的先生', 35],
    ['中文數字（僅十位）', '二十歲', 20],
    ['中文數字（十開頭省略一）', '十五歲', 15],
    ['中文數字（個位）', '九歲', 9],
    ['民國年次', '民國78年次', 37],
    ['西元年生', '1989年生', 37],
  ])('%s：%s → %i 歲', (_label, text, expected) => {
    expect(extractAge(text, TODAY)).toBe(expected);
  });

  it('「歲數」不該被誤判成年齡', () => {
    expect(extractAge('請填寫歲數欄位', TODAY)).toBeNull();
  });
});

/**
 * ⚠️ 已知問題：Python 版的 `compute_combination_risk()` 無法注入日期。
 *
 * 民國年次與西元年生要換算成年齡，Python 版內部直接呼叫 `date.today()`，
 * 沒有開放參數注入。這代表**建議文字會隨系統日期改變**：
 * 「民國78年次」在 2026 年產生「37歲」，2027 年變成「38歲」。
 *
 * 後果是任何含這類寫法的快照測試都是時間炸彈——跨年那天會全組一起紅，
 * 而且訊息完全看不出跟日期有關。這與 CONTRIBUTING 鎖死套件版本要防的
 * 是同一類問題（不可重現）。
 *
 * 因此 `tests/fixtures/parity_cases.json` 刻意**不放**民國年次/西元年生的語料，
 * 改由這裡用注入日期的方式測。TypeScript 版已支援注入（`today` 參數），
 * 已在 PR 中建議 Python 版比照辦理，屆時就能納入 parity 快照。
 */
describe('日期相依性（Python 版目前無法注入日期，見上方說明）', () => {
  it('TypeScript 版可注入日期，同一段文字在不同年份得到不同年齡', () => {
    const text = '民國78年次';
    expect(extractAge(text, new Date('2026-08-25T00:00:00Z'))).toBe(37);
    expect(extractAge(text, new Date('2027-08-25T00:00:00Z'))).toBe(38);
  });
});
