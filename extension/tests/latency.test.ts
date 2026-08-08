/**
 * 規則層延遲量測 — 對應專題風險清單 R2（瀏覽器內延遲 > 1 秒使用者就不用）。
 *
 * 這裡量的是**規則層**（checksum + regex）的延遲。語意層（NER 模型）
 * 之後接上時要另外量，兩者相加才是使用者實際感受到的等待時間。
 * 規則層的預算應該壓在整體的極小部分，把餘裕留給模型。
 */

import { describe, expect, it } from 'vitest';

import { detectAll } from '../src/core';

/** 產生一份混合中文敘述與各類個資的長文，模擬「整份契約 / 病歷貼上」的最壞情況。 */
function buildDocument(paragraphs: number): string {
  const block = [
    '甲方王小明，身分證字號 A123456789，聯絡電話 0912-345-678，',
    '通訊地址設於台北市大安區某路某號，電子郵件 wang@example.com.tw。',
    '乙方為某某股份有限公司，統一編號 12345675，市內電話 02-23456789，',
    '負責人身分證 F131104093，公司信箱 contact@company.com.tw。',
    '本合約自簽訂日起生效，雙方應遵守個人資料保護法之相關規定，',
    '未經他方書面同意不得將本合約所載個人資料提供予第三人。',
  ].join('');
  return Array.from({ length: paragraphs }, () => block).join('\n\n');
}

describe('規則層延遲', () => {
  it('一般貼上長度（約 2 千字）遠低於 1 秒預算', () => {
    const text = buildDocument(10);
    const started = performance.now();
    const { spans } = detectAll(text);
    const elapsed = performance.now() - started;

    expect(spans.length).toBeGreaterThan(0);
    // 預算抓 100ms：規則層只能用掉 1 秒預算的一小部分，其餘留給語意層
    expect(elapsed).toBeLessThan(100);
  });

  it('極端長度（約 2 萬字）仍在 1 秒內完成', () => {
    const text = buildDocument(100);
    const started = performance.now();
    detectAll(text);
    const elapsed = performance.now() - started;

    expect(elapsed).toBeLessThan(1000);
  });

  it('偵測結果不隨文件長度線性劣化到不可用（抽樣觀察）', () => {
    const measure = (paragraphs: number) => {
      const text = buildDocument(paragraphs);
      const started = performance.now();
      detectAll(text);
      return performance.now() - started;
    };

    // 暖機，避免第一次執行的 JIT 成本混進量測
    measure(10);

    const small = measure(10);
    const large = measure(100);

    console.log(
      `[延遲] 2 千字：${small.toFixed(2)} ms ／ 2 萬字：${large.toFixed(2)} ms`,
    );

    expect(large).toBeLessThan(1000);
  });
});
