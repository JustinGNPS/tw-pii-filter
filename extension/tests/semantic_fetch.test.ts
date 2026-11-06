import { describe, expect, it, vi } from 'vitest';

import { fetchSemanticPayload } from '../src/background/semantic_fetch';
import { SEMANTIC_ENDPOINT } from '../src/semantic';

describe('background localhost request', () => {
  it('使用固定 localhost 端點與識別標頭', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ spans: [] }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    await fetchSemanticPayload('王小明', fetchMock as typeof fetch);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(SEMANTIC_ENDPOINT);
    expect(init?.headers).toMatchObject({
      'content-type': 'application/json',
      'x-tw-pii-client': 'chrome-extension',
    });
    expect(JSON.parse(String(init?.body))).toEqual({ text: '王小明' });
  });

  it('保留 Proxy 提供的可操作錯誤訊息', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({
      detail: { code: 'ner_disabled', message: '請重新啟動並開啟語意層' },
    }), { status: 409 }));
    await expect(fetchSemanticPayload('王小明', fetchMock as typeof fetch))
      .rejects.toThrow('請重新啟動並開啟語意層');
  });
});
