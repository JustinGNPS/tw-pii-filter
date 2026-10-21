import { describe, expect, it } from 'vitest';
import { detectAll } from '../src/core';
import { CONFIGURABLE_TYPES, configurableTypes, filterEnabled } from '../src/settings';

const TEXT = '身分證 A123456789，手機 0912-345-678，信箱 test@example.com';

describe('filterEnabled', () => {
  it('全部開啟時什麼都不濾', () => {
    const spans = detectAll(TEXT).spans;
    expect(filterEnabled(spans, new Set(CONFIGURABLE_TYPES))).toEqual(spans);
  });

  it('關掉的類型當作沒偵測到，其他照常', () => {
    const spans = detectAll(TEXT).spans;
    const kept = filterEnabled(spans, new Set(CONFIGURABLE_TYPES.filter((t) => t !== 'TW_ID')));
    expect(kept.map((s) => s.type).sort()).toEqual(['EMAIL', 'TW_PHONE_M']);
  });

  it('全部關掉就是空的（貼上時直接放行、不跳面板）', () => {
    expect(filterEnabled(detectAll(TEXT).spans, new Set())).toEqual([]);
  });

  it('不在可設定清單裡的類型（例如未來的語意層）一律保留，不會被舊設定默默關掉', () => {
    const fake = [{ start: 0, end: 3, type: 'NAME', text: '王小明', confidence: 0.9, source: 'model', replacement: '[NAME_1]' }];
    expect(filterEnabled(fake as never, new Set())).toHaveLength(1);
  });
});

describe('configurableTypes', () => {
  it('8 種規則層類型都有中文名稱', () => {
    const list = configurableTypes();
    expect(list).toHaveLength(8);
    for (const { code, label } of list) expect(label).not.toBe(code);
  });
});
