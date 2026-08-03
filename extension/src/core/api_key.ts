/**
 * API Key / Token（API_KEY）常見樣式判斷與偵測。
 * 移植自 Python 版 `core/rules/api_key.py`，行為必須完全一致。
 */

import type { DetectionResult, Span } from './types';
import { anchored, findIter, makeRuleSpan } from './util';

/**
 * 涵蓋常見的 API key / token 前綴樣式（依特定前綴優先於較泛用的前綴排列）：
 *   sk-ant-...   Anthropic API key
 *   sk-proj-...  OpenAI project key
 *   sk-...       泛用 sk- 前綴（如舊版 OpenAI 風格）
 *   ghp_...      GitHub Personal Access Token
 *   AKIA...      AWS Access Key ID
 *   AIza...      Google API key
 *   xox[a-z]-... Slack token（xoxb-、xoxp- 等）
 *   eyJ...       JWT（header.payload.signature，header 固定以 eyJ 開頭）
 */
const TOKEN_ALTERNATION =
  'sk-ant-[A-Za-z0-9_-]{20,}' +
  '|sk-proj-[A-Za-z0-9_-]{20,}' +
  '|sk-[A-Za-z0-9]{20,}' +
  '|ghp_[A-Za-z0-9]{30,40}' +
  '|AKIA[0-9A-Z]{16}' +
  '|AIza[A-Za-z0-9_-]{20,}' +
  '|xox[a-z]-[A-Za-z0-9-]{10,}' +
  '|eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+';

/** 前後不可緊接英數字、底線或連字號，避免截斷更長的字串。 */
const TOKEN_PATTERN = new RegExp(
  `(?<![A-Za-z0-9_-])(?:${TOKEN_ALTERNATION})(?![A-Za-z0-9_-])`,
  'g',
);

const TOKEN_FULLMATCH = anchored(`(?:${TOKEN_ALTERNATION})`);

/**
 * 常見「標籤 = 值」樣式，如 API_KEY=xxx、token=xxx、password=xxx。
 * 只擷取值本身（group 1）作為偵測片段，不含標籤與等號。
 *
 * 註：Python 版用 `(?i)` 內嵌旗標，JavaScript 不支援內嵌旗標，改用 `i` 旗標。
 * `d` 旗標（hasIndices）用來取得 group 1 的起訖位置，對應 Python 的
 * `match.start(1)` / `match.end(1)`。
 */
const ASSIGNMENT_PATTERN =
  /\b(?:API[_-]?KEY|TOKEN|PASSWORD)\s*=\s*['"]?([A-Za-z0-9_\-./+]{6,})['"]?/gid;

/** 判斷字串是否符合常見 API key / token 樣式（前綴型樣式，如 sk-、AKIA、JWT 等）。 */
export function isValidApiKey(keyStr: unknown): boolean {
  if (typeof keyStr !== 'string') return false;
  return TOKEN_FULLMATCH.test(keyStr.trim());
}

/**
 * 在文字中找出所有 API key / token。
 *
 * 來源包含：已知前綴樣式（sk-ant-、sk-proj-、sk-、ghp_、AKIA、AIza、xox[a-z]-、JWT）
 * 以及「標籤 = 值」的泛用樣式（API_KEY=、token=、password=）。
 * 若兩種來源在文字中判定到重疊區間（例如 API_KEY=sk-xxx 這種賦值本身就是已知前綴樣式），
 * 只保留其中一個，避免同一段文字被重複標記。
 */
export function detectApiKey(text: string): DetectionResult {
  const candidates: Array<[number, number, string]> = [];

  for (const match of findIter(TOKEN_PATTERN, text)) {
    candidates.push([match.index, match.index + match[0].length, match[0]]);
  }

  for (const match of findIter(ASSIGNMENT_PATTERN, text)) {
    const groupRange = match.indices?.[1];
    if (!groupRange) continue;
    candidates.push([groupRange[0], groupRange[1], match[1]]);
  }

  // 依起始位置排序，起始位置相同時優先保留較長的片段
  // （JS 的 Array.prototype.sort 自 ES2019 起保證穩定，與 Python sorted 一致）
  candidates.sort((a, b) => a[0] - b[0] || (b[1] - b[0]) - (a[1] - a[0]));

  const spans: Span[] = [];
  let count = 0;
  let lastEnd = -1;
  for (const [start, end, candidate] of candidates) {
    if (start < lastEnd) continue;
    count += 1;
    spans.push(makeRuleSpan(start, end, 'API_KEY', candidate, 0.9, count));
    lastEnd = end;
  }
  return { text, spans };
}
