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
 * 語意層（Layer 2）模型定義的**完整** 14 種標籤。
 *
 * 來源不是「測試句子撞見過哪些」，而是直接讀模型 `config.json` 的 `id2label`
 * （去掉 BIO 前綴後取聯集），與 `core/ner/get_model_labels.py` 同一份清單。
 *
 * ## 這是通用領域中文 NER，不是 PII 專用模型
 *
 * `BOOK` / `GAME` / `MOVIE` / `SCENE` 根本不是個資，`EMAIL` / `MOBILE` 則與
 * 規則層重複（規則層有格式驗證、模型沒有）。B 在 08-14 用真實流量實測，
 * 這些雜訊型別造成的不是隱私問題而是**功能損害**——一個反引號被遮成
 * 佔位符送出去，agent 的 system prompt 被挖洞。
 *
 * 因此系統只「採信」其中 4 種，見 `NER_ALLOW_TYPES`。這裡仍列出全部 14 種，
 * 是為了讓讀的人知道「還有這些東西可能跑出來」——把清單藏起來正是
 * 全組沿用「只有 4 種」這個錯誤說法 11 天的原因。
 *
 * ⚠️ 仍未列進 `docs/interface.md` 的類別代碼表（issue #30 追蹤中）。
 * 補上後這裡應以文件為準。
 */
export type ModelPiiType =
  // 系統採信的（見 NER_ALLOW_TYPES）
  | 'NAME'
  | 'ADDRESS'
  | 'POSITION'
  | 'COMPANY'
  // 模型會產出但不採信的雜訊型別
  | 'ORGANIZATION'
  | 'GOVERNMENT'
  | 'EMAIL'
  | 'MOBILE'
  | 'QQ'
  | 'VX'
  | 'BOOK'
  | 'GAME'
  | 'MOVIE'
  | 'SCENE';

/**
 * 語意層採信的型別白名單——**不在這裡面的 span 當作沒偵測到**。
 *
 * 對應 proxy 的 `config.NER_ALLOW_TYPES`（PR #28），預設值必須一致。
 *
 * ## 與「偵測到但不遮蔽」是兩個不同機制，不要混用
 *
 * | | 白名單（本常數） | 預設不勾選（面板的 UI 決定） |
 * |---|---|---|
 * | 意思 | 根本不可信，當作沒偵測到 | 偵測對，但預設不遮 |
 * | 進 Layer 3 組合風險分數 | ❌ 不進 | ✅ 會進 |
 * | 算進「偵測到 N 項」 | ❌ 不算 | ✅ 會算 |
 *
 * `COMPANY` 正是需要區分兩者的例子：它**留在白名單內**（要計入組合風險，
 * 權重 0.15，排除掉會漏報），但在面板上可以是「預設不勾選」。
 */
export const NER_ALLOW_TYPES: ReadonlySet<string> = new Set([
  'NAME',
  'ADDRESS',
  'POSITION',
  'COMPANY',
]);

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

/** Layer 3 組合風險評分的風險等級，對應 docs/interface.md「組合風險評分」一節。 */
export type RiskLevel = '高' | '中' | '低';

/**
 * Layer 3：組合風險評分結果。對應 Python 版
 * `core/risk/combination_risk.py::compute_combination_risk()` 與
 * `docs/layer3_spec.md`。
 */
export interface CombinationRisk {
  /** 風險分數，0.0–1.0 */
  score: number;
  /** 造成風險的準識別子類別，依字母排序、去重 */
  contributing_types: string[];
  risk_level: RiskLevel;
  /** 對應每個 contributing_types 的泛化建議 */
  suggestions: string[];
}

export interface DetectionResult {
  /** 原始輸入文字，未經修改 */
  text: string;
  /** 偵測到的片段，依 start 升序排列 */
  spans: Span[];
  /**
   * Layer 3 組合風險評分（選填欄位，見 docs/interface.md）；
   * 沒有組合風險（或尚未計算，見 `detectAll()` 的說明）時為 `null`。
   *
   * 型別設為選填（`?`）對應 Python 版的不對稱：個別 `detect_xxx()` 偵測器
   * 的回傳值本來就沒有這個鍵（Layer 3 只在 `detect_all()`/`detectAll()`
   * 合併、仲裁完 spans 之後才計算），只有 `detectAll()` 一律會設值
   * （沒有風險時明確設為 `null`，而不是省略）。
   */
  combination_risk?: CombinationRisk | null;
}

/** 單一偵測器的函式簽章。 */
export type Detector = (text: string) => DetectionResult;
