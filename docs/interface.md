# 偵測結果介面（Detection Result Interface）

本文件定義全隊共用的 PII 偵測介面，供 B、C 開發對應模組時遵循。任何實作（rule-based 或 model-based）都必須回傳此格式，以確保各模組可互相組合、串接。

## 函式簽章

```
detect(text: str) -> dict
```

- `text`：待偵測的原始文字。
- 回傳值：符合下方 JSON Schema 的物件。

## 回傳格式

```json
{
  "text": "原始文字",
  "spans": [
    {
      "start": 0,
      "end": 10,
      "type": "TW_ID",
      "text": "偵測到的原文",
      "confidence": 0.95,
      "source": "rule",
      "replacement": "[TW_ID_1]"
    }
  ],
  "combination_risk": null
}
```

### 頂層欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| `text` | string | 原始輸入文字，未經修改 |
| `spans` | array | 偵測到的 PII 片段列表，依 `start` 升序排列 |
| `combination_risk` | object \| null | Layer 3 組合風險評分（選填欄位），見下方「組合風險評分（`combination_risk`，Layer 3）」；沒有組合風險時為 `null` |

### `spans[]` 欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| `start` | int | 片段在 `text` 中的起始**字元索引**（character offset，0-indexed，含） |
| `end` | int | 片段在 `text` 中的結束**字元索引**（character offset，不含，符合 Python slice 慣例）——即 `text[start:end]` 必須等於該筆偵測的 `text` 欄位內容 |
| `type` | string | 類別代碼，見下方「類別代碼」 |
| `text` | string | 偵測到的原文片段 |
| `confidence` | float | 信心值，範圍 `0.0`–`1.0` |
| `source` | string | 偵測來源，`"rule"` 或 `"model"` |
| `replacement` | string | 建議替換文字，格式為 `[<type>_<序號>]`，例如 `[TW_ID_1]`；同一 `text` 中同類別的第 N 筆偵測，序號從 1 開始遞增 |

## 類別代碼（`type`）

### 規則層（8 種）

| 代碼 | 說明 |
|---|---|
| `TW_ID` | 台灣身分證字號 |
| `TW_TAX` | 統一編號 |
| `TW_NHI` | 健保卡號 |
| `TW_PHONE_M` | 台灣手機號碼 |
| `TW_PHONE_L` | 台灣市內電話（含區碼） |
| `EMAIL` | 電子郵件地址 |
| `CREDIT_CARD` | 信用卡號 |
| `API_KEY` | API 金鑰 |

規則層有正則與檢核碼驗證，**不受下面語意層的過濾機制影響**，一律照常偵測。

### 語意層（NER）：三層要分清楚

語意層的型別代碼**有三個不同的集合，數量不一樣**。看文件或讀程式時務必確認
自己講的是哪一層 —— 混淆這三層正是 PR #28 修掉的那個問題的成因。

#### 第 1 層：模型原始標籤（14 種）

語意層用的 `gyr66/bert-base-chinese-finetuned-ner` 是**通用領域**中文 NER 模型，
訓練標籤共 14 種（用 `python core/ner/get_model_labels.py` 可查）：

```
QQ, address, book, company, email, game, government,
mobile, movie, name, organization, position, scene, vx
```

模型原生輸出是**小寫**，經 `core/redact/mapping.normalize_type()` 正規化為大寫。

#### 第 2 層：系統實際採信的型別（白名單 4 種）

```
NAME, ADDRESS, POSITION, COMPANY
```

由 `proxy/config.py` 的 `NER_ALLOW_TYPES` 控制（環境變數 `PII_NER_ALLOW_TYPES`，
預設即這 4 種）。過濾發生在 `proxy/detector.py::_keep_allowed_types()`，位置在
`detect_all(extra_spans=...)` 的**上游** —— 白名單外的 span 從一開始就不存在，
不參與 Layer 4 仲裁、不計入 log 筆數、不計入組合風險分數。

**下游能拿到的語意層 `type` 只有這 4 種。**

#### 第 3 層：被排除的雜訊型別（其餘 10 種）

```
QQ, BOOK, EMAIL, GAME, GOVERNMENT, MOBILE, MOVIE, ORGANIZATION, SCENE, VX
```

排除的理由是**實測**，不是預設立場：通用領域中文模型拿去掃 agent 送來的**英文
技術文字**（system prompt、程式碼、工具說明）時，產出的幾乎全是雜訊。用真實
Claude Code 請求（36,428 字元）實測，語意層在它自己的 system prompt 上判出：

```
GAME         x6   'here'  'ollama'  '—'  'MEMORY'  '`'  'Environment\nYou'
ORGANIZATION x2   'mistral'  '`'
```

**一個反引號被判成 `GAME`、遮成佔位符送給上游。** 這不是隱私問題（那些本來就
不是個資），是**功能損害**：agent 的系統提示被挖洞，行為會受影響。

排除動作發生在兩個地方：

| 型別 | 在哪裡被丟掉 | 原因 |
|---|---|---|
| `EMAIL`、`MOBILE` | `core/ner/detector.py` 的 `EXCLUDED_TYPES`（PR #26） | 語意層對這兩種只抓得到破碎片段（單獨的 `@`、單獨的 `.com`），規則層用正則處理得又快又準 |
| 其餘 8 種 | `proxy/detector.py` 的白名單過濾（PR #28） | 通用領域標籤，與個人身分無關 |

### 為什麼要分三層寫

**因為這個專案有 11 天的時間都以為語意層只有 4 種標籤**（`NAME`/`ADDRESS`/
`POSITION`/`COMPANY`），直到 2026-08-14 才去核對模型本身，才發現實際有 14 種
（PR #28）。那 11 天裡多出來的 10 種標籤是**靜默生效**的：它們照常被偵測、
照常被遮蔽，只是沒有人知道。

把三層分開寫死在這裡，是為了讓之後接手或換模型的人不會重蹈覆轍：

- 只看到第 2 層的人，會以為模型只認得 4 種標籤，換模型時不會想到要重新核對標籤集
- 只看到第 1 層的人，會以為下游拿得到 14 種型別，寫出永遠不會被觸發的分支
- 用**白名單**而不是黑名單，就是因為黑名單要求我們預先知道模型的所有標籤 ——
  白名單在日後換模型、模型改標籤集時都不會被新標籤偷襲

**換模型時的檢查清單**：跑 `core/ner/get_model_labels.py` 取得新模型的完整標籤集
→ 更新本節第 1 層 → 決定哪些進白名單 → 更新 `DEFAULT_NER_ALLOW_TYPES` → 更新本節
第 2、3 層。

### ⚠️ 已知不一致：組合風險評分認得的型別比白名單多

`core/risk/combination_risk.py` 的 `QUASI_IDENTIFIER_TYPES` 包含
`ORGANIZATION`、`GOVERNMENT`、`SCENE`（見下方「準識別子的來源」），但這三種都在
第 3 層、會被白名單擋掉，**在 proxy 這條路徑上永遠不會出現在 `spans` 裡**，
因此它們的權重與建議文字目前是死碼。

這不是 bug，是兩個決定先後做出來的結果（組合風險評分先，白名單後），但如果之後
有人想讓機構名稱參與組合風險評分，要**同時**把它加進 `PII_NER_ALLOW_TYPES`，
只改 `combination_risk.py` 不會有任何效果。

## 範例

輸入：

```
王小明的身分證字號是 A123456789，信箱是 test@example.com
```

輸出：

```json
{
  "text": "王小明的身分證字號是 A123456789，信箱是 test@example.com",
  "spans": [
    {
      "start": 9,
      "end": 19,
      "type": "TW_ID",
      "text": "A123456789",
      "confidence": 0.99,
      "source": "rule",
      "replacement": "[TW_ID_1]"
    },
    {
      "start": 24,
      "end": 40,
      "type": "EMAIL",
      "text": "test@example.com",
      "confidence": 0.95,
      "source": "rule",
      "replacement": "[EMAIL_1]"
    }
  ]
}
```

## 規則層與語意層的整合約定

本專案的偵測結果來自兩層，最終都要匯流成同一份符合本介面格式的 `spans`：

- **規則層**（B/C 開發，`core/rules/`）：rule-based 偵測器，每筆 span 的 `source` 固定標記為 `"rule"`。
- **語意層**（D 開發的 NER model）：model-based 偵測器，直接回傳它自己判斷出的原始結果（不必自行處理重疊），每筆 span 的 `source` 固定標記為 `"model"`。

兩層各自產生的 spans 會交給 `core.rules.detect_all(text, extra_spans=None)` 統一整合：

```
detect_all(text, extra_spans=None) -> dict
```

- `text`：待偵測的原始文字。
- `extra_spans`：語意層（或其他外部來源）已產生、符合本介面格式的 spans 清單（`source` 應為 `"model"`）；預設 `None` 表示只跑規則層。

`detect_all` 會把規則層內部所有偵測器的結果，加上 `extra_spans` 傳入的語意層結果，合併成一份清單，再交給 Layer 4（見下）統一仲裁重疊、輸出單一結果。

## Layer 4：重疊衝突解析（`detect_all`）

規則層與語意層各自運作時，同一段文字可能被多個偵測器同時抓到、產生重疊的 span（例如手機號碼剛好是 email local-part 的一部分）。`detect_all` 在合併規則層與語意層的結果後，會執行 Layer 4 衝突解析（實作於 `core/rules/conflict_resolver.py`），**保證回傳的 `spans` 彼此互不重疊**。

重疊時的仲裁順序（由上而下比較，前一條分不出勝負才比下一條）：

1. **範圍大者優先**：涵蓋字元數（`end - start`）較大的 span 勝出。
2. **confidence 高者優先**：範圍相同時，`confidence` 較高的 span 勝出。
3. **`source` 優先權**：範圍與 confidence 都相同時，`source == "rule"` 優先於 `source == "model"`。

輸給仲裁的 span 會被整筆移除（不做合併）。仲裁完成後，`replacement` 欄位會依 `type` 分組**重新編號**（從 1 開始連續遞增），確保不會因為某筆 span 在仲裁中被移除，而讓留下來的 span 停留在過時、跳號的序號（例如只剩 `TW_ID_2` 卻沒有 `TW_ID_1`）。最終結果依 `start`（相同時再依 `end`）排序。

範例：輸入 `"聯絡我 a0912345678@gmail.com"`，`EMAIL` 抓到 `a0912345678@gmail.com`（長度 21），`TW_PHONE_M` 抓到裡面的 `0912345678`（長度 10），兩者重疊；依規則 1，範圍較大的 `EMAIL` 勝出，`TW_PHONE_M` 被移除，`detect_all` 最終只回傳 `EMAIL` 這一筆。

> 注意：只有 `detect_all` 保證無重疊、無跳號。若 B、C 直接呼叫個別偵測器的 `detect_xxx(text)`，回傳的 spans **未經**此仲裁與重新編號，彼此仍可能重疊。

## 組合風險評分（`combination_risk`，Layer 3）

Layer 1（規則層）與 Layer 2（語意層）抓的是「明確的個資」；Layer 3 抓的是另一類問題：單獨看都不是個資、但組合起來能定位到特定個人的「準識別子」（例如「32歲」+「新竹」+「資深後端工程師」）。完整規格見 `docs/layer3_spec.md`，實作於 `core/risk/combination_risk.py::compute_combination_risk(text, spans)`。

`detect_all` 在 Layer 4 仲裁完成、`spans` 確定之後，會把 `text` 與最終的 `spans` 一起交給 `compute_combination_risk()`，計算結果放進頂層的 `combination_risk` 欄位：

```json
{
  "score": 0.85,
  "contributing_types": ["ADDRESS", "AGE", "POSITION"],
  "risk_level": "高",
  "suggestions": [
    "「32歲」建議泛化為「30-34歲」",
    "地址建議泛化到市/縣級（例如「信義區光復路259巷」→「台北市」）",
    "職稱可保留，但建議避免同時透露服務公司名稱"
  ]
}
```

### `combination_risk` 欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| `score` | float | 風險分數，`0.0`–`1.0`，由 `contributing_types` 各自的識別力權重加總後封頂 |
| `contributing_types` | array\<string\> | 造成風險的準識別子類別，依字母排序、去重 |
| `risk_level` | string | 風險等級：`"高"` / `"中"` / `"低"` |
| `suggestions` | array\<string\> | 對應每個 `contributing_types` 的泛化建議（把精確值換成範圍） |

### 選填、可為 `null`

**只有** `text`/`spans` 是 `detect_all` 一定會回傳的欄位。`combination_risk` 屬選填欄位，計算後若 `score` 為 `0`（準識別子共現數 `< 2`，單一準識別子不構成組合風險）則整個欄位為 `null`，而不是回傳 `score: 0` 的空殼物件——下游只要檢查 `combination_risk` 是否為 `null`，就能判斷這段文字有沒有組合風險，不需要再檢查 `score` 是否為 `0`。

### 準識別子的來源

`contributing_types` 目前涵蓋的類別分兩種來源：

- **語意層 spans**：`ADDRESS` / `POSITION` / `COMPANY` / `ORGANIZATION` / `GOVERNMENT` / `SCENE`——來自 `extra_spans`（Layer 2 語意層結果），`compute_combination_risk` 直接檢查 Layer 4 仲裁後的 `spans` 裡有沒有這些型別
- **獨立正則偵測**：`AGE` / `GENDER`——語意層模型沒有對應標籤，`compute_combination_risk` 自己對 `text` 做正則/關鍵字掃描，不需要透過 `spans` 帶入

> ⚠️ `ORGANIZATION` / `GOVERNMENT` / `SCENE` 這三種雖然列在這裡，但已被語意層白名單
> 排除（見上方「類別代碼」第 3 層），在 proxy 路徑上永遠不會出現在 `spans` 裡。

因此若呼叫 `detect_all(text)` 時沒有傳入 `extra_spans`（未接語意層），`contributing_types` 只可能包含 `AGE`/`GENDER`；要看到 `ADDRESS`/`POSITION` 這類準識別子造成的風險，呼叫端必須先把語意層（`core.ner.detect_ner()`）的結果當 `extra_spans` 傳入。

## 約定事項

- `detect_all` 回傳的 `spans` 保證互不重疊，已完成 Layer 4 仲裁，下游可以安全地直接依座標（`start`/`end`）替換文字，不需要自行去重。
- 個別偵測器的 `detect_xxx(text)`（未經 `detect_all` 整合）之間仍可能輸出重疊片段，這是預期行為；若要單獨串接個別偵測器、又需要去重，可另外呼叫 `core.rules.conflict_resolver.resolve_overlaps(spans)`。
- `source` 僅允許 `"rule"` 或 `"model"` 兩種值：規則層固定 `"rule"`，語意層固定 `"model"`；供後續統計、除錯區分偵測來源，也是 Layer 4 仲裁順序第三條的依據。
- `replacement` 的序號只在 `detect_all` 完成 Layer 4 仲裁後才具有「連續不跳號」的保證；個別偵測器單獨回傳的 `replacement` 序號僅代表該偵測器自己在整段文字中的偵測順序。
- 新增類別代碼時，應同步更新本文件並知會全隊。
