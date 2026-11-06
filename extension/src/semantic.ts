/** 本機 Proxy 語意層的訊息協定與回應驗證。 */

import { NER_ALLOW_TYPES, type Span } from './core';

export const SEMANTIC_MESSAGE = 'TW_PII_NER_SCAN';
export const SEMANTIC_ENDPOINT = 'http://127.0.0.1:8000/extension/ner';
export const SEMANTIC_TIMEOUT_MS = 12_000;

export interface SemanticResult {
  spans: Span[];
  detectMs: number;
}
interface RuntimeReply {
  ok?: boolean;
  data?: unknown;
  error?: string;
}

/** Python 的 code-point 索引轉成 JavaScript 的 UTF-16 索引。 */
export function codePointIndexToUtf16(text: string, codePointIndex: number): number {
  return Array.from(text).slice(0, codePointIndex).join('').length;
}

/**
 * 驗證 Proxy 回應並重建完整 Span。任何欄位不合理都拒絕整包結果，避免錯位遮蔽
 * 把姓名的一半留在原文裡，卻讓使用者誤以為已受保護。
 */
export function decodeSemanticResponse(text: string, payload: unknown): SemanticResult {
  if (!payload || typeof payload !== 'object') throw new Error('本機語意層回應格式錯誤');
  const body = payload as Record<string, unknown>;
  if (body.offset_encoding !== 'unicode-codepoints') {
    throw new Error('本機語意層回傳未知的文字座標格式');
  }
  if (!Array.isArray(body.spans)) throw new Error('本機語意層沒有回傳 spans');

  const codePointLength = Array.from(text).length;
  const spans = body.spans.map((item, index): Span => {
    if (!item || typeof item !== 'object') throw new Error(`語意結果第 ${index + 1} 筆格式錯誤`);
    const raw = item as Record<string, unknown>;
    const start = raw.start;
    const end = raw.end;
    const type = raw.type;
    const confidence = raw.confidence;
    if (!Number.isInteger(start) || !Number.isInteger(end)) {
      throw new Error(`語意結果第 ${index + 1} 筆缺少有效座標`);
    }
    if ((start as number) < 0 || (end as number) <= (start as number) || (end as number) > codePointLength) {
      throw new Error(`語意結果第 ${index + 1} 筆座標超出原文`);
    }
    if (typeof type !== 'string' || !NER_ALLOW_TYPES.has(type)) {
      throw new Error(`語意結果第 ${index + 1} 筆包含未採信型別`);
    }
    if (typeof confidence !== 'number' || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      throw new Error(`語意結果第 ${index + 1} 筆缺少有效信心值`);
    }
    if (raw.source !== 'model') throw new Error(`語意結果第 ${index + 1} 筆來源錯誤`);

    const utf16Start = codePointIndexToUtf16(text, start as number);
    const utf16End = codePointIndexToUtf16(text, end as number);
    return {
      start: utf16Start,
      end: utf16End,
      type,
      text: text.slice(utf16Start, utf16End),
      confidence,
      source: 'model',
      // detectAll() 會在 Layer 4 仲裁後重新編號；這裡只需提供合法格式。
      replacement: `[${type}_1]`,
    };
  });

  const timing = body.timing && typeof body.timing === 'object'
    ? (body.timing as Record<string, unknown>).detect_ms
    : 0;
  return { spans, detectMs: typeof timing === 'number' && Number.isFinite(timing) ? timing : 0 };
}

/** 透過 background service worker 呼叫 localhost，content script 不直接碰網路。 */
export async function requestSemanticScan(
  text: string,
  timeoutMs = SEMANTIC_TIMEOUT_MS,
): Promise<SemanticResult> {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timeoutId = setTimeout(() => reject(new Error('本機語意分析逾時')), timeoutMs);
  });
  try {
    const reply = await Promise.race([
      chrome.runtime.sendMessage({ type: SEMANTIC_MESSAGE, text }) as Promise<RuntimeReply>,
      timeout,
    ]);
    if (!reply?.ok) throw new Error(reply?.error || '無法連線本機語意服務');
    return decodeSemanticResponse(text, reply.data);
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
  }
}
