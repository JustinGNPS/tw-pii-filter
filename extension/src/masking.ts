/**
 * 遮蔽與對照表建立。
 *
 * 依照專題規劃「第一版只做偵測 + 警告，第二版才做還原」的分階段策略，
 * 這裡負責的是**送出前**的遮蔽：把偵測到的 span 換成佔位符，並同時
 * 產出本地對照表供第二版的還原機制使用（對照表絕不外傳）。
 *
 * ⚠️ 佔位符**不使用** `span.replacement`，改由 {@link PlaceholderAllocator} 配號，
 * 原因見 `src/placeholder.ts` 的說明（`detect_all()` 每次重新編號，跨次呼叫
 * 會讓同一個號碼對到不同真值）。
 */

import type { PiiType, Span } from './core';
import { PlaceholderAllocator } from './placeholder';

/** 風險等級，用於確認面板的視覺提示。 */
export type RiskLevel = 'high' | 'medium' | 'low';

/**
 * 各類別的風險等級與中文顯示名稱。
 *
 * 風險等級的判斷依據是「洩漏後的後果嚴重度」，不是偵測信心：
 * 身分證、統編、信用卡、金鑰一旦外流無法挽回，列為高風險；
 * 電話、健保卡號屬於可識別但衝擊較低，列為中風險。
 */
const TYPE_META: Record<string, { label: string; risk: RiskLevel }> = {
  // ── Layer 1 規則層 ──
  TW_ID: { label: '身分證字號', risk: 'high' },
  TW_TAX: { label: '統一編號', risk: 'high' },
  CREDIT_CARD: { label: '信用卡號', risk: 'high' },
  API_KEY: { label: 'API 金鑰 / Token', risk: 'high' },
  TW_NHI: { label: '健保卡號', risk: 'medium' },
  TW_PHONE_M: { label: '手機號碼', risk: 'medium' },
  TW_PHONE_L: { label: '市內電話', risk: 'medium' },
  EMAIL: { label: '電子郵件', risk: 'low' },

  // ── Layer 2 語意層（模型定義的完整 14 種，見 core/types.ts 的說明）──

  // 真正的個資
  NAME: { label: '人名', risk: 'medium' },
  ADDRESS: { label: '地址 / 地點', risk: 'medium' },
  // POSITION（職稱）、COMPANY / ORGANIZATION / GOVERNMENT（所屬單位）本身通常
  // 不算個資，但都是準識別子：「35 歲」+「新竹」+「資深後端工程師」不需要姓名
  // 就能指認到特定個人。這正是 Layer 3 組合風險評分要吃的資訊
  // （專題報告 4.3：隱含身分洩漏率 95%），在偵測層濾掉 Layer 3 就沒東西可算。
  // 因此保留偵測、列為低風險、預設勾選，由使用者自行決定。
  //
  // 註：proxy 端（B）的 normalize_type 預設跳過 POSITION 不遮蔽。兩個載體
  // 取捨不同是刻意的：proxy 沒有人在旁邊確認、誤遮會直接弄壞 agent 的程式碼；
  // 擴充有確認面板，使用者看得到也能取消勾選，可以更保守地多抓一些。
  POSITION: { label: '職稱', risk: 'low' },
  COMPANY: { label: '公司名稱', risk: 'low' },
  ORGANIZATION: { label: '組織名稱', risk: 'low' },
  GOVERNMENT: { label: '政府機關', risk: 'low' },

  // 通訊帳號
  QQ: { label: 'QQ 號碼', risk: 'medium' },
  VX: { label: '微信帳號', risk: 'medium' },

  // 與規則層重複——規則層有格式驗證、語意層沒有，重疊時 Layer 4 會讓規則層勝出。
  // 註：模型的 email 轉大寫後就是 `EMAIL`，與規則層同一個代碼（靠 `source`
  // 欄位區分來源），所以上面規則層那筆就涵蓋了，這裡只需要補 `MOBILE`。
  MOBILE: { label: '手機號碼', risk: 'medium' },

  // 非個資：通用中文 NER 標籤集附帶的娛樂／作品類別
  BOOK: { label: '書名', risk: 'low' },
  GAME: { label: '遊戲名稱', risk: 'low' },
  MOVIE: { label: '影劇名稱', risk: 'low' },
  SCENE: { label: '景點名稱', risk: 'low' },
};

/**
 * 模型會產出、但**不是個人資料**的類別。
 *
 * 所用的 NER 模型是 CLUENER 那一系的**通用**中文 NER，不是 PII 專用模型，
 * 標籤集裡附帶了書名、遊戲、影劇、景點這些娛樂／作品類別。
 * `core/ner/detector.py` 目前沒有做型別過濾，因此這些會實際進到偵測結果裡。
 *
 * 擴充的處理方式是**照樣顯示、但預設不勾選**，而不是靜靜濾掉：
 *  - 靜靜濾掉會造成兩個載體偵測結果不一致（proxy 沒濾），而載體分歧
 *    正是本專題已經踩過三次的坑
 *  - 預設不勾選就不會把「哈利波特」這種東西遮成 `[BOOK_1]`，
 *    使用者若真的想遮（例如書名本身是線索）仍然可以自己勾起來
 *
 * 根治方式是在來源端（`detector.py`）就過濾掉非 PII 型別，已在 PR 中請 D 評估。
 */
export const NON_PII_TYPES: ReadonlySet<string> = new Set([
  'BOOK',
  'GAME',
  'MOVIE',
  'SCENE',
]);

/**
 * 語意層與規則層涵蓋範圍重疊的類別。
 *
 * 規則層對這些有格式驗證（email 正則、手機號碼格式 + 09 開頭檢查），
 * 語意層只有模型的判斷。兩者重疊時 Layer 4 會依
 * 「範圍大 → confidence 高 → rule 優先」仲裁，通常規則層勝出。
 *
 * 列出來是為了記錄這個重疊關係（也是 review 語意層價值時的依據：
 * 這兩類語意層其實沒有加分），不是拿來過濾用的。
 */
export const RULE_LAYER_OVERLAP_TYPES: ReadonlySet<string> = new Set([
  'EMAIL',
  'MOBILE',
]);

/** 這個類別是否為模型附帶的非個資類別（面板據此決定要不要預設勾選）。 */
export function isNonPii(type: PiiType): boolean {
  return NON_PII_TYPES.has(type);
}

/** 語意層代碼還在變動，遇到沒見過的一律照原樣顯示、不要壞掉。 */
export function typeLabel(type: PiiType): string {
  return TYPE_META[type]?.label ?? type;
}

export function riskLevel(type: PiiType): RiskLevel {
  return TYPE_META[type]?.risk ?? 'medium';
}

/** 這個代碼是不是我們認識的（否則面板會標示「未知類別」提醒使用者自行判斷）。 */
export function isKnownType(type: PiiType): boolean {
  return type in TYPE_META;
}

/** 對照表的一筆紀錄：佔位符 ↔ 原文。第二版還原機制會用到。 */
export interface MappingEntry {
  placeholder: string;
  original: string;
  type: PiiType;
}

export interface MaskResult {
  /** 遮蔽後、實際會送給 AI 的文字 */
  maskedText: string;
  /** 佔位符 → 原文的對照表（僅存本地） */
  mapping: MappingEntry[];
}

/**
 * 依 spans 把原文遮蔽成佔位符版本。
 *
 * @param text      原始文字
 * @param spans     要遮蔽的 spans；必須互不重疊（`detectAll` 已保證）。
 *                  若呼叫端只勾選了部分項目，傳入篩選後的子集即可
 * @param allocator 佔位符配號器。傳入同一個 allocator 就能讓多次貼上、
 *                  多輪對話之間「同一個真值永遠對到同一個佔位符」
 *
 * 實作要點：由後往前替換，避免前面的替換改變後面 span 的 offset。
 */
export function maskText(
  text: string,
  spans: Span[],
  allocator: PlaceholderAllocator = new PlaceholderAllocator(),
): MaskResult {
  const sorted = [...spans].sort((a, b) => a.start - b.start || a.end - b.end);

  // 先一次配完號：同值必同碼由 allocator 保證（含跨次呼叫）
  const placeholders = sorted.map((span) => allocator.allocate(span.type, span.text));

  let maskedText = text;
  for (let i = sorted.length - 1; i >= 0; i -= 1) {
    const span = sorted[i];
    maskedText = maskedText.slice(0, span.start) + placeholders[i] + maskedText.slice(span.end);
  }

  const mapping: MappingEntry[] = [];
  const seen = new Set<string>();
  sorted.forEach((span, i) => {
    const placeholder = placeholders[i];
    if (seen.has(placeholder)) return;
    seen.add(placeholder);
    mapping.push({ placeholder, original: span.text, type: span.type });
  });

  return { maskedText, mapping };
}
