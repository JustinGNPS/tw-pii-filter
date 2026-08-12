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
 * 來源是 `core/ner/detector.py`：模型的 `entity_group` 經 `.upper()` 後輸出。
 * 先前這裡列的 `name` / `address` / `position` 小寫版與 `PERSON` / `LOCATION` /
 * `ORG` 都已作廢——小寫版在型別代碼統一轉大寫後不再出現（見下），
 * `PERSON` 那組則是實測前的推測，從未真正產生過。
 *
 * ⚠️ 這四個代碼**仍未列進 docs/interface.md 的類別代碼表**，
 * 而該文件自己要求「新增類別代碼時，應同步更新本文件並知會全隊」。
 * 目前三個模組各自從 PR 討論裡抄代碼，這正是本檔案上一版會過時的原因。
 * 已請 A 補進 interface.md；補上後這裡應以文件為準。
 */
export type ModelPiiType = 'NAME' | 'ADDRESS' | 'POSITION' | 'COMPANY';

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
