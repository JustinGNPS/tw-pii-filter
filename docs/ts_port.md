# 規則層 TypeScript 移植說明

## 為什麼需要兩套實作

瀏覽器擴充（載體一）跑在 Chrome 裡，**沒有 Python 執行環境**。因此規則層必須有兩套實作：

| 實作 | 位置 | 使用者 |
|---|---|---|
| Python | `core/rules/*.py` | B 的 LLM API Proxy（載體二） |
| TypeScript | `extension/src/core/*.ts` | C 的瀏覽器擴充（載體一） |

兩套實作對同一段輸入**必須產生完全相同的輸出**。一旦分歧，同一份文件在擴充與 proxy 會得到不同的遮蔽結果——這是最難察覺、也最傷公信力的 bug（使用者以為擋掉了，其實沒有）。

## 一致性怎麼保證

不靠人工比對，靠測試：

```
tests/fixtures/parity_cases.json     ← 共用語料（28 筆）
          │
          ├─ tools/gen_parity_expected.py  →  parity_expected.json（Python 版輸出快照）
          │
          └─ extension/tests/parity.test.ts →  逐筆比對 TypeScript 版輸出
```

**任一方改動規則層邏輯後，都必須重跑：**

```bash
python tools/gen_parity_expected.py     # 重新產生 Python 基準
cd extension && npm test                # 確認 TypeScript 版仍然一致
```

並把更新後的 `parity_expected.json` 一起 commit。

除了逐筆比對，`parity.test.ts` 另外驗證四件事：

- `text.slice(start, end) === span.text`（offset 正確性）
- `detectAll` 回傳的 spans 互不重疊且依 `start` 升序
- 每個 type 的 `replacement` 序號從 1 開始連續不跳號
- 語料與快照筆數一致（防止快照過期沒被發現）

## 移植時處理掉的 Python / JavaScript 差異

| Python | JavaScript | 處理方式 |
|---|---|---|
| `(?i)` 內嵌旗標 | 不支援 | 改用 `i` 旗標 |
| `re.fullmatch()` | 無對應方法 | `util.anchored()` 包成 `^(?:...)$` |
| `pattern.finditer()` | `matchAll` | `util.findIter()`，並每次重置 `lastIndex`（避免共用 RegExp 的殘留狀態） |
| `match.start(1)` / `match.end(1)` | 需 `d` 旗標 | `api_key.ts` 用 `d`（hasIndices）取 group 1 位置 |
| `sorted()` 穩定排序 | `Array.sort` 自 ES2019 起亦穩定 | 直接對應；但**偵測器執行順序必須一致**（見下） |

### ⚠️ 偵測器順序必須與 Python 一致

`core/rules/__init__.py` 的 `_DETECTORS` 與 `extension/src/core/index.ts` 的 `DETECTORS` **順序必須相同**。Layer 4 仲裁在「長度、confidence、source 全部平手」時依賴排序的穩定性，順序不同會讓兩版選到不同的 span。新增偵測器時請同步兩邊。

---

## 🔴 已知分歧：全形數字（待與 A 討論）

這是一致性測試在建立當天就抓到的**真實問題**，尚未修正。

### 現象

Python 的 `re` 對 `str` 套用 `\d` 時是 **Unicode-aware**，會匹配全形０-９；`str.isdigit()`、`int()` 同樣接受全形數字。JavaScript 的 `\d` 只匹配 ASCII `0-9`。

結果是 **Python 版自己就不一致**：

| 類別 | pattern | 全形輸入 Python | 全形輸入 TypeScript |
|---|---|---|---|
| `TW_TAX` | `\d{8}` | ✅ 抓得到 | ❌ 抓不到 |
| `TW_NHI` | `\d{12}` | ✅ 抓得到 | ❌ 抓不到 |
| `TW_ID` | `[A-Za-z]\d{9}` | ❌ 抓不到（首字母是 ASCII-only 字元類） | ❌ 抓不到 |
| `TW_PHONE_M` | `09\d{2}...` | ❌ 抓不到（開頭是字面 ASCII `09`） | ❌ 抓不到 |

驗證過的實例：

```python
>>> detect_all("統編 １２３４５６７５")
{'spans': [{'type': 'TW_TAX', 'text': '１２３４５６７５', ...}]}   # 有抓到
```

TypeScript 版對同一段輸入回傳 `spans: []`。

分歧已用 `extension/tests/known_divergence.test.ts` 釘住，一旦有人修好或改壞，測試會失敗並要求同步更新本文件。

### 為什麼這件事重要

全形數字在中文輸入環境是**真實會發生**的——Windows 注音輸入法的全形模式、從 Word 或 PDF 複製出來的文件都可能帶全形數字。使用者貼上一份含全形統編的合約，目前：

- 走 proxy（載體二）→ 擋得下來
- 走擴充（載體一）→ **擋不下來，個資直接送出去**

這不只是兩版不一致的潔癖問題，是實際的偵測缺口。

### 建議修法

**在偵測前統一做全形→半形正規化，兩版同步實作。**

- 全形英數（U+FF01–U+FF5E）對半形（U+0021–U+007E）是固定 offset 0xFEE0，轉換簡單
- 正規化後 `TW_ID`、`TW_PHONE_M` 也一併涵蓋，補掉 Python 現在也漏的兩類
- **必須保留 offset 對應表**：正規化後的座標要能映回原文，否則 `span.start` / `span.end` 會對不上使用者實際看到的文字，遮蔽會切錯位置
- 全形英數與半形是一對一映射，字元數不變，offset 對應是恆等映射——這讓實作比預期單純很多

這個修法會動到 Python 規則層，屬於 A 的範圍，需要先討論再動手。相關討論請開 issue 追蹤。

---

## 建置與測試

```bash
cd extension
npm install
npm run build      # 產出 dist/，到 chrome://extensions 載入未封裝項目
npm run dev        # watch 模式
npm test           # 一致性 + 分歧 + 延遲測試
npm run typecheck  # tsc --noEmit
```

## 延遲實測（對應風險清單 R2）

規則層在 Node 上的量測結果：

| 文件長度 | 耗時 |
|---|---|
| 約 2 千字 | 0.11 ms |
| 約 2 萬字 | 2.33 ms |

規則層對 1 秒預算的佔用可以忽略，**整個延遲預算實質上都留給語意層（NER 模型）**。這也代表 R2 的風險完全集中在模型端，而不是規則端。
