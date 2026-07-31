/**
 * 偵測結果型別 — 對應 docs/interface.md 定義的介面。
 *
 * 這份型別是 TypeScript 端的唯一真實來源（single source of truth），
 * 欄位名稱、語意與 Python 版 `core/rules` 完全一致，兩版必須可互換。
 */

/** 類別代碼，對應 docs/interface.md「類別代碼」一節。 */
export type PiiType =
  | 'TW_ID'
  | 'TW_TAX'
  | 'TW_NHI'
  | 'TW_PHONE_M'
  | 'TW_PHONE_L'
  | 'EMAIL'
  | 'CREDIT_CARD'
  | 'API_KEY';

/** 偵測來源：規則層固定 "rule"，語意層（NER）固定 "model"。 */
export type SpanSource = 'rule' | 'model';

export interface Span {
  /** 起始字元索引（0-indexed，含） */
  start: number;
  /** 結束字元索引（不含）；`text.slice(start, end)` 必須等於 `text` 欄位 */
  end: number;
  type: PiiType;
  /** 偵測到的原文片段 */
  text: string;
  /** 信心值 0.0–1.0 */
  confidence: number;
  source: SpanSource;
  /** 建議替換文字，格式 `[<type>_<序號>]` */
  replacement: string;
}

export interface DetectionResult {
  /** 原始輸入文字，未經修改 */
  text: string;
  /** 偵測到的片段，依 start 升序排列 */
  spans: Span[];
}

/** 單一偵測器的函式簽章。 */
export type Detector = (text: string) => DetectionResult;
