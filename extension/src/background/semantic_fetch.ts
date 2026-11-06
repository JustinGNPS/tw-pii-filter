/** background 專用：呼叫本機 Proxy，不記錄或回傳請求原文。 */

import { SEMANTIC_ENDPOINT } from '../semantic';

export async function fetchSemanticPayload(
  text: string,
  fetchImpl: typeof fetch = fetch,
): Promise<unknown> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12_000);
  try {
    const response = await fetchImpl(SEMANTIC_ENDPOINT, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-tw-pii-client': 'chrome-extension',
      },
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => null) as Record<string, unknown> | null;
    if (!response.ok) {
      const detail = data?.detail;
      const message = detail && typeof detail === 'object'
        ? (detail as Record<string, unknown>).message
        : detail;
      throw new Error(typeof message === 'string' ? message : `本機語意服務回應 ${response.status}`);
    }
    return data;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('本機語意分析逾時');
    }
    if (error instanceof TypeError) {
      throw new Error('找不到本機 Proxy；請先執行啟動.bat，選擇 [1] 並開啟語意層');
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
