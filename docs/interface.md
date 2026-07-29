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
  ]
}
```

### 頂層欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| `text` | string | 原始輸入文字，未經修改 |
| `spans` | array | 偵測到的 PII 片段列表，依 `start` 升序排列 |

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

## 約定事項

- `detect_all` 回傳的 `spans` 保證互不重疊，已完成 Layer 4 仲裁，下游可以安全地直接依座標（`start`/`end`）替換文字，不需要自行去重。
- 個別偵測器的 `detect_xxx(text)`（未經 `detect_all` 整合）之間仍可能輸出重疊片段，這是預期行為；若要單獨串接個別偵測器、又需要去重，可另外呼叫 `core.rules.conflict_resolver.resolve_overlaps(spans)`。
- `source` 僅允許 `"rule"` 或 `"model"` 兩種值：規則層固定 `"rule"`，語意層固定 `"model"`；供後續統計、除錯區分偵測來源，也是 Layer 4 仲裁順序第三條的依據。
- `replacement` 的序號只在 `detect_all` 完成 Layer 4 仲裁後才具有「連續不跳號」的保證；個別偵測器單獨回傳的 `replacement` 序號僅代表該偵測器自己在整段文字中的偵測順序。
- 新增類別代碼時，應同步更新本文件並知會全隊。
