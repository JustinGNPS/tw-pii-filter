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
| `start` | int | 片段在 `text` 中的起始位置（0-indexed，含） |
| `end` | int | 片段在 `text` 中的結束位置（不含，符合 Python slice 慣例，即 `text[start:end]` 即為 `text` 欄位內容） |
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

## 約定事項

- `spans` 之間不應重疊；若不同偵測器輸出重疊片段，由後續整合邏輯（非本介面範圍）負責去重或合併。
- `source` 僅允許 `"rule"` 或 `"model"` 兩種值，供後續統計與除錯區分偵測來源。
- 新增類別代碼時，應同步更新本文件並知會全隊。
