import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  codePointIndexToUtf16,
  decodeSemanticResponse,
  requestSemanticScan,
  SEMANTIC_MESSAGE,
} from '../src/semantic';

function response(overrides: Record<string, unknown> = {}) {
  return {
    offset_encoding: 'unicode-codepoints',
    spans: [
      { start: 0, end: 3, type: 'NAME', confidence: 0.98, source: 'model' },
    ],
    timing: { detect_ms: 42.5 },
    ...overrides,
  };
}

beforeEach(() => {
  vi.unstubAllGlobals();
});
describe('語意層回應驗證', () => {
  it('補回原文、replacement 與模型來源', () => {
    const result = decodeSemanticResponse('王小明您好', response());
    expect(result.detectMs).toBe(42.5);
    expect(result.spans[0]).toMatchObject({
      start: 0,
      end: 3,
      type: 'NAME',
      text: '王小明',
      confidence: 0.98,
      source: 'model',
      replacement: '[NAME_1]',
    });
  });

  it('把 Python code-point 座標轉成 JavaScript UTF-16 座標', () => {
    const text = '🙂王小明您好';
    expect(codePointIndexToUtf16(text, 1)).toBe(2);
    const result = decodeSemanticResponse(
      text,
      response({ spans: [{ start: 1, end: 4, type: 'NAME', confidence: 0.99, source: 'model' }] }),
    );
    expect(result.spans[0]).toMatchObject({ start: 2, end: 5, text: '王小明' });
  });

  it.each([
    [{ offset_encoding: 'utf16', spans: [] }, /座標格式/],
    [response({ spans: [{ start: 0, end: 99, type: 'NAME', confidence: 0.9, source: 'model' }] }), /超出原文/],
    [response({ spans: [{ start: 0, end: 3, type: 'GAME', confidence: 0.9, source: 'model' }] }), /未採信型別/],
    [response({ spans: [{ start: 0, end: 3, type: 'NAME', confidence: 2, source: 'model' }] }), /信心值/],
    [response({ spans: [{ start: 0, end: 3, type: 'NAME', confidence: 0.9, source: 'rule' }] }), /來源錯誤/],
  ])('不接受不可信回應 %#', (payload, pattern) => {
    expect(() => decodeSemanticResponse('王小明您好', payload)).toThrow(pattern as RegExp);
  });
});

describe('content/popup 訊息客戶端', () => {
  it('只透過 extension runtime 傳給 background', async () => {
    const sendMessage = vi.fn(async () => ({ ok: true, data: response() }));
    vi.stubGlobal('chrome', { runtime: { sendMessage } });
    const result = await requestSemanticScan('王小明您好', 1000);
    expect(result.spans[0].text).toBe('王小明');
    expect(sendMessage).toHaveBeenCalledWith({ type: SEMANTIC_MESSAGE, text: '王小明您好' });
  });

  it('background 回報失敗時不假裝成零筆結果', async () => {
    vi.stubGlobal('chrome', {
      runtime: { sendMessage: vi.fn(async () => ({ ok: false, error: 'Proxy 尚未開啟' })) },
    });
    await expect(requestSemanticScan('王小明', 1000)).rejects.toThrow('Proxy 尚未開啟');
  });
});
