import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConversationStateStore } from '../src/content/conversation_state';
import {
  emptyState,
  loadState,
  PlaceholderAllocator,
  purgeStoredMappings,
  STATE_TTL_MS,
} from '../src/placeholder';
import { reverseMap } from '../src/restore';

function stateFor(type: string, value: string) {
  const allocator = new PlaceholderAllocator(emptyState());
  allocator.allocate(type, value);
  return allocator.toState();
}

function installStorageMock(initial: Record<string, unknown> = {}) {
  const data: Record<string, unknown> = structuredClone(initial);
  const local = {
    get: vi.fn(async (key: string | string[] | null) => {
      if (key === null) return structuredClone(data);
      const keys = Array.isArray(key) ? key : [key];
      return Object.fromEntries(keys.filter((item) => item in data).map((item) => [item, structuredClone(data[item])]));
    }),
    set: vi.fn(async (items: Record<string, unknown>) => Object.assign(data, structuredClone(items))),
    remove: vi.fn(async (key: string | string[]) => {
      for (const item of Array.isArray(key) ? key : [key]) delete data[item];
    }),
  };
  vi.stubGlobal('chrome', { storage: { local } });
  return { data, local };
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('敏感對照表儲存安全', () => {
  it('讀到過期狀態時會真的從 storage 刪除', async () => {
    const key = 'ph:chatgpt.com/c/old';
    const expired = stateFor('NAME', '舊姓名');
    expired.updatedAt = Date.now() - STATE_TTL_MS - 1;
    const { data, local } = installStorageMock({ [key]: expired });

    expect((await loadState(key)).assigned).toEqual({});
    expect(local.remove).toHaveBeenCalledWith(key);
    expect(data[key]).toBeUndefined();
  });

  it('啟動清理會移除舊版 sessions 與所有過期或損壞的對話狀態', async () => {
    const now = Date.now();
    const fresh = stateFor('NAME', '保留');
    fresh.updatedAt = now;
    const expired = stateFor('NAME', '刪除');
    expired.updatedAt = now - STATE_TTL_MS - 1;
    const { data } = installStorageMock({
      sessions: [{ mapping: [{ original: '不應保留' }] }],
      'ph:chatgpt.com/c/fresh': fresh,
      'ph:chatgpt.com/c/expired': expired,
      'ph:chatgpt.com/c/broken': { nope: true },
      restoreEnabled: true,
    });

    expect(await purgeStoredMappings(now)).toBe(3);
    expect(Object.keys(data).sort()).toEqual(['ph:chatgpt.com/c/fresh', 'restoreEnabled']);
  });
});

describe('新對話分頁隔離', () => {
  it('兩個新對話分頁各自保留對照表，取得 ID 後不會互相還原錯人', async () => {
    installStorageMock();
    const tabA = new ConversationStateStore();
    const tabB = new ConversationStateStore();
    const newChat = new URL('https://chatgpt.com/');

    await tabA.save(newChat, stateFor('NAME', '王小明'));
    await tabB.save(newChat, stateFor('NAME', '陳美玲'));
    tabA.markNewChatSubmitted(newChat);
    tabB.markNewChatSubmitted(newChat);
    await tabA.adoptNewChatState(newChat, new URL('https://chatgpt.com/c/a'));
    await tabB.adoptNewChatState(newChat, new URL('https://chatgpt.com/c/b'));

    const stateA = await loadState('ph:chatgpt.com/c/a');
    const stateB = await loadState('ph:chatgpt.com/c/b');
    expect(reverseMap(stateA).get('[NAME_1]')).toBe('王小明');
    expect(reverseMap(stateB).get('[NAME_1]')).toBe('陳美玲');
    expect(await loadState('ph:chatgpt.com/')).toMatchObject({ assigned: {} });
  });

  it('未送出就切到沒有本機對照表的舊對話時也不會搬移暫存', async () => {
    installStorageMock();
    const tab = new ConversationStateStore();
    const newChat = new URL('https://chatgpt.com/');
    const oldChat = new URL('https://chatgpt.com/c/old-without-local-state');

    await tab.save(newChat, stateFor('NAME', '未送出的暫存姓名'));
    expect(await tab.adoptNewChatState(newChat, oldChat)).toBe(false);

    expect(reverseMap(await tab.load(newChat)).size).toBe(0);
    expect(reverseMap(await loadState('ph:chatgpt.com/c/old-without-local-state')).size).toBe(0);
  });

  it('確認送出後才把新對話暫存搬到新取得的對話 ID', async () => {
    installStorageMock();
    const tab = new ConversationStateStore();
    const newChat = new URL('https://chatgpt.com/');
    const createdChat = new URL('https://chatgpt.com/c/created');

    await tab.save(newChat, stateFor('NAME', '已送出的姓名'));
    expect(tab.markNewChatSubmitted(newChat)).toBe(true);
    expect(await tab.adoptNewChatState(newChat, createdChat)).toBe(true);

    expect(reverseMap(await loadState('ph:chatgpt.com/c/created')).get('[NAME_1]')).toBe('已送出的姓名');
  });

  it('未送出就切到既有對話時會丟棄暫存，之後的新對話不會沿用舊資料', async () => {
    const existing = stateFor('NAME', '既有對話的人');
    installStorageMock({ 'ph:chatgpt.com/c/existing': existing });
    const tab = new ConversationStateStore();
    const newChat = new URL('https://chatgpt.com/');

    await tab.save(newChat, stateFor('NAME', '未送出的暫存姓名'));
    expect(await tab.adoptNewChatState(newChat, new URL('https://chatgpt.com/c/existing'))).toBe(false);

    expect(reverseMap(await tab.load(newChat)).size).toBe(0);
    expect(reverseMap(await loadState('ph:chatgpt.com/c/existing')).get('[NAME_1]')).toBe('既有對話的人');
  });

  it('從新對話跨站離開時會丟棄暫存，不會帶到另一個服務', async () => {
    installStorageMock();
    const tab = new ConversationStateStore();
    const newChat = new URL('https://chatgpt.com/');

    await tab.save(newChat, stateFor('NAME', '不該跨站的人'));
    tab.markNewChatSubmitted(newChat);
    expect(await tab.adoptNewChatState(newChat, new URL('https://claude.ai/chat/abc'))).toBe(false);

    expect(reverseMap(await tab.load(newChat)).size).toBe(0);
    expect(reverseMap(await loadState('ph:claude.ai/chat/abc')).size).toBe(0);
  });
});
