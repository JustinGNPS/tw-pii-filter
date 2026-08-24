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

## ✅ 已修復：全形數字（原本是兩版最大的分歧）

**這一節記錄的問題已於 issue #21 / #27 修復。** 保留在文件裡是因為它的成因與
修法值得留存——這是一致性測試在建立當天就抓到的第一個真實缺口。

### 原本的問題

Python 的 `re` 對 `str` 套用 `\d` 時是 Unicode-aware，會匹配全形０-９；
JavaScript 的 `\d` 只認 ASCII。結果是 **Python 版自己就不一致**：

| 類別 | pattern | 修復前 Python | 修復前 TypeScript |
|---|---|---|---|
| `TW_TAX` | `\d{8}` | ✅ | ❌ |
| `TW_NHI` | `\d{12}` | ✅ | ❌ |
| `TW_ID` | `[A-Za-z]\d{9}` | ❌（首字母是 ASCII-only 字元類） | ❌ |
| `TW_PHONE_M` | `09\d{2}...` | ❌（開頭是字面 ASCII `09`） | ❌ |
| `EMAIL` | `[A-Za-z0-9._%+-]+@...` | ❌ | ❌ |

也就是說全形手機、身分證、信箱**兩層都沒有覆蓋**——B 在 #27 確認語意層
也補不上（全形手機只抓到單一個全形數字字元、還誤標成 `EMAIL`）。

### 修法：偵測前做全形→半形正規化

- `core/rules/normalize.py`（Python）與 `extension/src/core/normalize.ts`（TypeScript）
- **只對全形英數（U+FF01–FF5E）與全形空格做定點映射**，不用 `NFKC`
- 原因：NFKC 不保證長度不變（`㍿` → `株式会社` 由 1 變 4、`ﬁ` → `fi` 由 1 變 2），
  長度一變座標就對不回原文，而遮蔽是「從後往前依座標替換」，會直接把文字切壞
- 定點映射是嚴格 1:1、字元數保證不變，因此座標可直接沿用，不需要維護對應表
- `span.text` 一律取自**原文**，維持 `text[start:end] == span["text"]` 的約定——
  使用者在面板上看到的是自己打的全形原文，不是被改寫過的半形版本

修復後四種型別（`TW_TAX` / `TW_ID` / `TW_PHONE_M` / `EMAIL`）全形寫法全部
偵測得到，兩版行為一致，原本的分歧語料已移入 `tests/fixtures/parity_cases.json`
由正式的一致性測試把關。

測試：`tests/test_normalize.py`（Python）、
`extension/tests/known_divergence.test.ts`（TypeScript）。

### 分歧語料機制仍然保留

`divergence_cases.json` / `divergence_python.json` / `known_divergence.test.ts`
這套機制留著沒刪（目前語料為空）。下次再發現兩版行為不同、而且一時無法或
不該立刻修時，把語料丟進去就能把分歧「釘住」，讓它不會默默漂移。

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
