/**
 * 已知的 Python ↔ TypeScript 行為分歧（全形數字）。
 *
 * 這批案例**刻意**不放進 parity.test.ts，因為兩版目前確實不一致。
 * 本測試把分歧「釘住」在已記錄的樣子：一旦有人修好（或改壞），測試就會失敗，
 * 強迫更新 docs/ts_port.md 與這份測試，避免分歧默默漂移。
 *
 * 成因：
 *   - Python `re` 對 str 套用 `\d` 時是 Unicode-aware，會匹配全形０-９；
 *     `str.isdigit()`、`int()` 同樣接受全形數字。
 *   - JavaScript 的 `\d` 只匹配 ASCII `0-9`。
 *
 * 結果是 Python 版自己就不一致：
 *   - TW_TAX（`\d{8}`）、TW_NHI（`\d{12}`）→ 全形抓得到
 *   - TW_ID（`[A-Za-z]\d{9}`）→ 首字母是 ASCII-only 字元類，全形 Ａ 抓不到
 *   - TW_PHONE_M（`09\d{2}...`）→ 開頭是字面 ASCII "09"，全形 ０９ 抓不到
 *
 * 建議修法（需與 A 討論後同步改兩版，見 docs/ts_port.md）：
 *   在偵測前統一做全形→半形正規化，並保留 offset 對應表，
 *   讓兩版對全形輸入都能完整偵測，而不是現在這種各抓一半。
 */

import { describe, expect, it } from 'vitest';

import { detectAll } from '../src/core';
import cases from '../../tests/fixtures/divergence_cases.json';
import pythonResults from '../../tests/fixtures/divergence_python.json';

/** Python 版對每筆語料抓到的 type（由 tools/gen_parity_expected.py 產生的快照推導）。 */
const PYTHON_TYPES = pythonResults.map((result) => result.spans.map((span) => span.type));

describe('已知分歧：全形數字', () => {
  it('TypeScript 版對全形數字一律不偵測（\\d 為 ASCII-only）', () => {
    for (const text of cases) {
      expect(detectAll(text).spans).toEqual([]);
    }
  });

  it('Python 版全形統編抓得到，TypeScript 抓不到', () => {
    const index = cases.indexOf('統編 １２３４５６７５');
    expect(index).toBeGreaterThanOrEqual(0);
    expect(PYTHON_TYPES[index]).toEqual(['TW_TAX']);
    expect(detectAll(cases[index]).spans).toEqual([]);
  });

  it('Python 版全形健保卡號抓得到，TypeScript 抓不到', () => {
    const index = cases.indexOf('健保卡 ００００１２３４５６７８');
    expect(index).toBeGreaterThanOrEqual(0);
    expect(PYTHON_TYPES[index]).toEqual(['TW_NHI']);
    expect(detectAll(cases[index]).spans).toEqual([]);
  });

  it('全形身分證與手機：兩版都抓不到（Python 自身的不一致）', () => {
    for (const text of ['身分證 Ａ１２３４５６７８９', '手機 ０９１２３４５６７８']) {
      const index = cases.indexOf(text);
      expect(index).toBeGreaterThanOrEqual(0);
      expect(PYTHON_TYPES[index]).toEqual([]);
      expect(detectAll(text).spans).toEqual([]);
    }
  });

  it('分歧語料的快照筆數與語料一致（防止快照過期）', () => {
    expect(pythonResults.length).toBe(cases.length);
  });
});
