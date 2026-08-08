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

  // ── Layer 2 語意層（來源：core/ner/detector.py，entity_group 轉大寫後輸出）──
  NAME: { label: '人名', risk: 'medium' },
  ADDRESS: { label: '地址 / 地點', risk: 'medium' },
  // POSITION（職稱）與 COMPANY（公司名稱）本身通常不算個資，但都是準識別子：
  // 「35 歲」+「新竹」+「資深後端工程師」不需要姓名就能指認到特定個人，
  // 這正是 Layer 3 組合風險評分要吃的資訊（專題報告 4.3：隱含身分洩漏率 95%）。
  // 因此保留偵測但列為低風險，預設勾選、由使用者自行決定要不要遮蔽——
  // 若在偵測層就濾掉，Layer 3 之後會沒有東西可算。
  //
  // 註：proxy 端（B）的 normalize_type 預設跳過 POSITION 不遮蔽。兩個載體
  // 取捨不同是刻意的：proxy 沒有人在旁邊確認、誤遮會直接弄壞 agent 的程式碼；
  // 擴充有確認面板，使用者看得到也能取消勾選，可以更保守地多抓一些。
  POSITION: { label: '職稱', risk: 'low' },
  COMPANY: { label: '公司名稱', risk: 'low' },
};

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
