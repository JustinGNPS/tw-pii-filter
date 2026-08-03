/**
 * 偵測結果型別 — 對應 docs/interface.md 定義的介面。
 *
 * 這份型別是 TypeScript 端的唯一真實來源（single source of truth），
 * 欄位名稱、語意與 Python 版 `core/rules` 完全一致，兩版必須可互換。
 */

/** 規則層（Layer 1）的類別代碼，對應 docs/interface.md「類別代碼」一節。 */
export type RulePiiType =
  | 'TW_ID'
  | 'TW_TAX'
  | 'TW_NHI'
  | 'TW_PHONE_M'
  | 'TW_PHONE_L'
  | 'EMAIL'
  | 'CREDIT_CARD'
  | 'API_KEY';

/**
 * 語意層（Layer 2）的類別代碼。
 *
 * ⚠️ 這些代碼**還沒進 docs/interface.md**，命名也尚未定案（PR #3 討論中）：
 * D 實測 `gyr66/bert-base-chinese-finetuned-ner` 的 `entity_group` 實際回傳的是
 * `name` / `address` / `position`，不是先前推測的 `PERSON` / `LOCATION` / `ORG`。
 * 這裡兩套都先列上，等 interface.md 定案後收斂成一套。
 */
export type ModelPiiType =
  | 'name'
  | 'address'
  | 'position'
  | 'PERSON'
  | 'LOCATION'
  | 'ORG';

/**
 * 類別代碼。
 *
 * 刻意保留 `(string & {})` 讓未知代碼不會在型別層被擋掉——語意層的類別清單
 * 還在變動中，擴充遇到沒見過的代碼時應該「照樣顯示、標為未知」而不是壞掉。
 * 顯示名稱與風險等級的 fallback 見 `src/masking.ts`。
 */
export type PiiType = RulePiiType | ModelPiiType | (string & {});

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
