import { describe, expect, it } from 'vitest';
import { detectAll } from '../src/core';
import { findNearMisses } from '../src/explain';

const misses = (text: string) => findNearMisses(text, detectAll(text).spans);

describe('findNearMisses', () => {
  it('使用者實測那句：三個都沒抓到，兩個號碼要說明原因', () => {
    const text = '我的名字是陳威成，生分證O123456789，統編:12345678';
    expect(detectAll(text).spans).toHaveLength(0);
    const out = misses(text);
    expect(out.map((m) => m.value)).toEqual(['O123456789', '12345678']);
    expect(out[0].reason).toContain('檢核碼');
    expect(out[1].reason).toContain('統一編號');
  });

  it('合法的號碼已經被偵測到，不會再列成被擋下', () => {
    expect(misses('身分證 A123456789，統編 12345675')).toEqual([]);
  });

  it('合法與不合法並存時只列不合法的', () => {
    const out = misses('A123456789 與 A123456788');
    expect(out.map((m) => m.value)).toEqual(['A123456788']);
  });

  it('市話後 8 碼單看像統編，但已被偵測為市話，不能誤報（以座標判斷）', () => {
    expect(misses('市話 02-27208889')).toEqual([]);
  });

  it('身分證位數不足', () => {
    expect(misses('身分證 A12345678')[0].reason).toContain('位數不足');
  });

  it('手機長度不對', () => {
    expect(misses('手機 091234567')[0].reason).toContain('長度不符');
  });

  it('沒有任何像個資的東西就不提示（純中文不吵）', () => {
    expect(misses('我叫王小明，住在台北，今天天氣很好')).toEqual([]);
  });

  it('同一個值出現兩次只列一次', () => {
    expect(misses('O123456789 再一次 O123456789')).toHaveLength(1);
  });
});
