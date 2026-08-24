/**
 * Layer 3：組合風險評分（TypeScript 版）。
 *
 * 移植自 Python 版 `core/risk/combination_risk.py`，行為必須完全一致。
 * 規格見 `docs/layer3_spec.md` 與 `docs/interface.md`「組合風險評分」一節。
 *
 * 核心概念（專題報告 4.3）：llm-redactor 論文實測，就算把明顯的 PII 字串
 * 都遮掉，「隱含身分」洩漏率仍達 95%——沒寫姓名，但寫了「35歲、新竹、
 * 資深後端工程師」，組合起來還是能定位到特定人。
 *
 * 對擴充來說這一層特別重要：確認面板列的是**逐項**的偵測結果，天生看不出
 * 「單項都不算個資、組合起來可以指認」這件事。使用者貼一段沒有身分證也
 * 沒有電話的自我介紹，面板若只顯示「未偵測到敏感資訊」就是誤導。
 */

import type { CombinationRisk, RiskLevel, Span } from './types';

/**
 * 各準識別子類別的權重，依「平均能把母體縮小多少」排序。
 * ⚠️ 必須與 Python 版 `WEIGHT_BY_TYPE` 完全一致。
 */
const WEIGHT_BY_TYPE: Record<string, number> = {
  AGE: 0.35,
  ADDRESS: 0.3,
  POSITION: 0.2,
  GENDER: 0.15,
  COMPANY: 0.15,
  ORGANIZATION: 0.15,
  GOVERNMENT: 0.15,
  SCENE: 0.1,
};

const QUASI_IDENTIFIER_TYPES = new Set(Object.keys(WEIGHT_BY_TYPE));

const RISK_SCORE_CAP = 1.0;

/** 達到這個分數就值得對使用者提出警告。 */
export const WARNING_THRESHOLD = 0.6;

// ── 年齡偵測：語意層模型沒有 AGE 標籤，這一層自己對 text 做正則掃描 ──

const AGE_DIGIT_PATTERN = /(?<![0-9])([1-9][0-9]?)\s*歲(?!數)/;
const AGE_MINGUO_PATTERN = /民國\s*([0-9]{1,3})\s*年(?:次|生)?/;
const AGE_WESTERN_YEAR_PATTERN = /((?:19|20)[0-9]{2})\s*年生/;

const CN_DIGIT_MAP: Record<string, number> = {
  零: 0, 一: 1, 二: 2, 兩: 2, 三: 3, 四: 4,
  五: 5, 六: 6, 七: 7, 八: 8, 九: 9,
};
const AGE_CHINESE_PATTERN = /([一二兩三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九])歲/;

/** 把「三十五」「二十」「九」這類中文數字（0～99）轉成整數，轉不了回 null。 */
function chineseNumberToInt(cn: string): number | null {
  if (!cn) return null;
  if (cn in CN_DIGIT_MAP) return CN_DIGIT_MAP[cn];
  if (cn.includes('十')) {
    const index = cn.indexOf('十');
    const left = cn.slice(0, index);
    const right = cn.slice(index + 1);
    // 「十五」的「十」前面沒數字，視為 1
    const tens = left ? (CN_DIGIT_MAP[left] ?? 1) : 1;
    const ones = right ? (CN_DIGIT_MAP[right] ?? 0) : 0;
    return tens * 10 + ones;
  }
  return null;
}

/**
 * 從文字裡抓出一個具體年齡數字（供泛化建議用）。抓不到回 null
 * （仍可能判定「有 AGE 這個準識別子」，只是沒有精確數字可以泛化）。
 *
 * @param today 測試可注入固定日期，避免民國年/西元年推算的結果隨時間改變
 */
export function extractAge(text: string, today: Date = new Date()): number | null {
  const digit = AGE_DIGIT_PATTERN.exec(text);
  if (digit) return Number(digit[1]);

  const minguo = AGE_MINGUO_PATTERN.exec(text);
  if (minguo) return today.getFullYear() - (Number(minguo[1]) + 1911);

  const western = AGE_WESTERN_YEAR_PATTERN.exec(text);
  if (western) return today.getFullYear() - Number(western[1]);

  const chinese = AGE_CHINESE_PATTERN.exec(text);
  if (chinese) return chineseNumberToInt(chinese[1]);

  return null;
}

function hasAge(text: string): boolean {
  return (
    AGE_DIGIT_PATTERN.test(text) ||
    AGE_MINGUO_PATTERN.test(text) ||
    AGE_WESTERN_YEAR_PATTERN.test(text) ||
    AGE_CHINESE_PATTERN.test(text)
  );
}

const GENDER_KEYWORDS = ['男性', '女性', '先生', '小姐', '太太', '女士'];

function hasGender(text: string): boolean {
  return GENDER_KEYWORDS.some((keyword) => text.includes(keyword));
}

function ageGeneralizationSuggestion(text: string, today?: Date): string {
  const age = extractAge(text, today);
  if (age === null) {
    return '文字中的年齡資訊建議泛化為 5 歲一個區間（例如「32歲」→「30-35歲」）';
  }
  const bucketStart = Math.floor(age / 5) * 5;
  return `「${age}歲」建議泛化為「${bucketStart}-${bucketStart + 4}歲」`;
}

const GENERIC_SUGGESTIONS: Record<string, string> = {
  ADDRESS: '地址建議泛化到市/縣級（例如「信義區光復路259巷」→「台北市」）',
  POSITION: '職稱可保留，但建議避免同時透露服務公司名稱',
  GENDER: '若非必要，建議省略性別資訊',
  COMPANY: '公司名稱可模糊化為產業別（例如「某科技公司」）',
  ORGANIZATION: '機構名稱可模糊化為機構類型',
  GOVERNMENT: '政府機關名稱可模糊化為機關層級（例如「某地方政府機關」）',
  SCENE: '常去地點建議降低描述精確度',
};

function buildSuggestions(text: string, contributingTypes: string[], today?: Date): string[] {
  const suggestions: string[] = [];
  for (const type of contributingTypes) {
    if (type === 'AGE') {
      suggestions.push(ageGeneralizationSuggestion(text, today));
    } else {
      const suggestion = GENERIC_SUGGESTIONS[type];
      if (suggestion) suggestions.push(suggestion);
    }
  }
  return suggestions;
}

/** 對應 layer3_spec.md 的三級分類。 */
function toRiskLevel(score: number): RiskLevel {
  if (score >= WARNING_THRESHOLD) return '高';
  if (score >= 0.3) return '中';
  return '低';
}

/** 這筆組合風險是否達到值得警告的門檻。 */
export function isWarningWorthy(risk: CombinationRisk | null | undefined): boolean {
  return (risk?.score ?? 0) >= WARNING_THRESHOLD;
}

/**
 * 計算一份文字的組合風險分數。
 *
 * ⚠️ `text` 與 `spans` 必須是同一份「視角」下的內容。這個函式評估的是
 * 「你給的這份 text，實際看得到多少準識別子」，不是「這段內容理論上曾經
 * 含有什麼」——因此內部才會直接對 text 本身做 AGE/GENDER 正則掃描，
 * 而不是只信任 spans。
 *
 * 兩種合法用法：
 *  - `text` = 原文、`spans` = 原文的完整偵測結果
 *    → 回答「這段內容本身潛在風險多高」
 *  - `text` = 遮蔽後的文字、`spans` = 沒被遮掉、仍留在文字裡的準識別子
 *    → 回答「送出去的內容，遮蔽完之後還剩多少風險」（載體端警告使用者該用這個）
 *
 * 錯誤用法：`text` = 遮蔽後文字、`spans` = 原文的完整偵測結果——
 * 兩者視角不一致，會把已經遮掉的準識別子也算進分數，虛報風險。
 *
 * @param today 測試可注入固定日期（見 {@link extractAge}）
 */
export function computeCombinationRisk(
  text: string,
  spans?: Span[] | null,
  today?: Date,
): CombinationRisk {
  const contributingTypes = new Set<string>();

  if (spans) {
    for (const span of spans) {
      if (QUASI_IDENTIFIER_TYPES.has(span.type)) {
        contributingTypes.add(span.type);
      }
    }
  }

  if (hasAge(text)) contributingTypes.add('AGE');
  if (hasGender(text)) contributingTypes.add('GENDER');

  // 單一準識別子不構成組合風險——組合風險的定義就是「多個併在一起」
  let score = 0;
  if (contributingTypes.size >= 2) {
    let total = 0;
    for (const type of contributingTypes) {
      total += WEIGHT_BY_TYPE[type] ?? 0.15;
    }
    score = Math.min(RISK_SCORE_CAP, total);
  }

  const sorted = [...contributingTypes].sort();

  return {
    // 對齊 Python 的 round(score, 3)
    score: Math.round(score * 1000) / 1000,
    contributing_types: sorted,
    risk_level: toRiskLevel(score),
    suggestions: buildSuggestions(text, sorted, today),
  };
}
