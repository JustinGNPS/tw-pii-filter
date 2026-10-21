import { describe, expect, it } from 'vitest';
import { maskText } from '../src/masking';
import { PlaceholderAllocator } from '../src/placeholder';
import { isNewChatPath, restoreString, reverseMap, TOKEN_PATTERN } from '../src/restore';
import { detectAll } from '../src/core';

function stateFor(pairs: Array<[string, string]>) {
  const allocator = new PlaceholderAllocator();
  for (const [type, value] of pairs) allocator.allocate(type, value);
  return allocator.toState();
}

describe('reverseMap', () => {
  it('從配號狀態反查出當初配發的佔位符', () => {
    const map = reverseMap(stateFor([['TW_ID', 'A123456789'], ['TW_ID', 'B123456780'], ['NAME', '王小明']]));
    expect(map.get('[TW_ID_1]')).toBe('A123456789');
    expect(map.get('[TW_ID_2]')).toBe('B123456780');
    expect(map.get('[NAME_1]')).toBe('王小明');
    expect(map.size).toBe(3);
  });

  it('原文含 tab 時只切第一個', () => {
    const map = reverseMap(stateFor([['NAME', 'a\tb']]));
    expect(map.get('[NAME_1]')).toBe('a\tb');
  });

  it('沒有狀態就是空表', () => {
    expect(reverseMap(null).size).toBe(0);
    expect(reverseMap(undefined).size).toBe(0);
  });
});

describe('restoreString', () => {
  const map = reverseMap(stateFor([['TW_ID', 'A123456789'], ['TW_PHONE_M', '0912345678']]));

  it('把已知佔位符換回真值，同一個佔位符出現多次都換', () => {
    const r = restoreString('客戶 [TW_ID_1] 的電話 [TW_PHONE_M_1]，再確認一次 [TW_ID_1]', map);
    expect(r.text).toBe('客戶 A123456789 的電話 0912345678，再確認一次 A123456789');
    expect(r.restored).toBe(3);
    expect(r.unknown).toBe(0);
  });

  it('查不到的佔位符原樣保留，絕不猜測（決定 5）', () => {
    const r = restoreString('AI 自己編的 [TW_ID_9] 不能亂填', map);
    expect(r.text).toBe('AI 自己編的 [TW_ID_9] 不能亂填');
    expect(r.restored).toBe(0);
    expect(r.unknown).toBe(1);
  });

  it('segments 指向輸出字串中換進去的真值，供標色用', () => {
    const r = restoreString('[TW_ID_1]、[TW_ID_9]、[TW_PHONE_M_1]', map);
    expect(r.segments.map((s) => r.text.slice(s.start, s.end))).toEqual(['A123456789', '0912345678']);
  });

  it('小寫或格式不對的不是佔位符', () => {
    const r = restoreString('[tw_id_1] [TW_ID_] [TW_ID_x] TW_ID_1', map);
    expect(r.restored).toBe(0);
    expect(r.unknown).toBe(0);
  });

  it('正則與 Python core/redact/mapping.TOKEN_PATTERN 一致', () => {
    expect(TOKEN_PATTERN.source).toBe('\\[([A-Z][A-Z_]*)_(\\d+)\\]');
  });
});

describe('遮蔽 → 還原 閉環', () => {
  it('同一個 allocator 的狀態能把自己遮出去的東西完整還原', () => {
    const text = '身分證 A123456789，手機 0912-345-678，統編 12345675，信箱 test@example.com';
    const allocator = new PlaceholderAllocator();
    const { maskedText } = maskText(text, detectAll(text).spans, allocator);
    expect(maskedText).not.toContain('A123456789');

    const r = restoreString(maskedText, reverseMap(allocator.toState()));
    expect(r.text).toBe(text);
    expect(r.unknown).toBe(0);
  });

  it('另一個對話的狀態不能拿來還原（同一個 [TW_ID_1] 是不同的人）', () => {
    const a = stateFor([['TW_ID', 'A123456789']]);
    const b = stateFor([['TW_ID', 'B123456780']]);
    expect(restoreString('[TW_ID_1]', reverseMap(a)).text).toBe('A123456789');
    expect(restoreString('[TW_ID_1]', reverseMap(b)).text).toBe('B123456780');
  });
});

describe('isNewChatPath', () => {
  it('只認三個網站的新對話頁', () => {
    expect(isNewChatPath('/')).toBe(true); // ChatGPT
    expect(isNewChatPath('/new')).toBe(true); // Claude
    expect(isNewChatPath('/app')).toBe(true); // Gemini
  });

  it('既有對話一律不算，避免把 A 對話的對照表搬到 B 對話', () => {
    expect(isNewChatPath('/c/6720ab12-0000')).toBe(false);
    expect(isNewChatPath('/chat/1234')).toBe(false);
    expect(isNewChatPath('/app/abcd')).toBe(false);
    expect(isNewChatPath('/g/g-xyz')).toBe(false);
  });
});
