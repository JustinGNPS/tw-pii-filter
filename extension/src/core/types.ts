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
 * 語意層（Layer 2）的類別代碼——模型定義的**完整** 14 種。
 *
 * 來源不是「測試句子撞見過哪些」，而是直接讀模型的 `config.json` 的
 * `id2label`（去掉 BIO 前綴後取聯集），與 `core/ner/get_model_labels.py` 同一份清單。
 * `core/ner/detector.py` 只把 `entity_group` 轉大寫，**沒有做型別過濾**，
 * 因此這 14 種都可能實際出現在 `detect_all()` 的輸出裡。
 *
 * ## ⚠️ 這個模型是通用中文 NER，不是 PII 專用模型
 *
 * 標籤集看得出來是 CLUENER 那一系的通用中文 NER：`BOOK` / `GAME` / `MOVIE` /
 * `SCENE` 這四種**根本不是個資**，`EMAIL` / `MOBILE` 則與規則層重複
 * （而且規則層有格式驗證、模型沒有）。分類見 `src/masking.ts` 的
 * `NON_PII_TYPES` 與 `RULE_LAYER_DUPLICATE_TYPES`。
 *
 * ⚠️ 這些代碼**仍未列進 docs/interface.md 的類別代碼表**，
 * 而該文件自己要求「新增類別代碼時，應同步更新本文件並知會全隊」。
 * 目前三個模組各自從 PR 討論裡抄代碼，這正是本檔案上一版會過時的原因
 * （上一版只列了 4 種，漏掉 10 種）。補進文件後這裡應以文件為準。
 */
export type ModelPiiType =
  // 真正的個資
  | 'NAME'
  | 'ADDRESS'
  | 'COMPANY'
  | 'ORGANIZATION'
  | 'GOVERNMENT'
  | 'POSITION'
  // 與規則層重複（規則層有格式驗證，語意層沒有）
  | 'EMAIL'
  | 'MOBILE'
  // 通訊帳號
  | 'QQ'
  | 'VX'
  // 非個資：通用 NER 標籤集附帶的娛樂/作品類別
  | 'BOOK'
  | 'GAME'
  | 'MOVIE'
  | 'SCENE';

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
