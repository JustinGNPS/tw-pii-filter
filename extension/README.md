# 瀏覽器擴充（載體一）

貼上文字到 AI 網頁之前，於本地端偵測台灣個資並提示遮蔽。資料不經任何第三方伺服器。

## 快速開始

```bash
cd extension
npm install
npm run build
```

到 `chrome://extensions` → 開啟右上角「開發人員模式」→「載入未封裝項目」→ 選 `extension/dist`。

## 兩種使用方式

**1. Popup（最快的 demo 方式）**

點瀏覽器工具列的擴充圖示，貼上文字即時看到偵測結果、遮蔽後內容與耗時。不需要開任何 AI 網站。

**2. 在 AI 網頁上自動攔截**

到 ChatGPT / Claude / Gemini，在輸入框按 Ctrl+V 貼上含個資的文字，會跳出確認面板。也可以選取輸入框裡的文字後按右鍵，選「🛡 隱私處理後貼上」。

## 為什麼主要入口是 paste 而不是右鍵選單

規劃書原本設定右鍵選單為主。實作時改成攔截 `paste` 事件，原因：

1. **權限**：右鍵選單拿不到剪貼簿內容，得額外要 `clipboardRead` 權限，上架審查會被追問，也讓「最小權限」的訴求打折。攔截 paste 事件用 `event.clipboardData`，不需要任何額外權限。
2. **體驗**：真正危險的情境是「沒多想就 Ctrl+V」——而右鍵選單保護不到沒多想的人。攔截 paste 讓使用者照常操作就被保護，零學習成本。

右鍵選單保留為次要入口，處理已經打在輸入框裡的文字。

## 目錄結構

```
extension/
├── src/
│   ├── core/          規則層 TypeScript 版（對應 Python 的 core/rules）
│   ├── content/       content script：paste 攔截、確認面板、文字插入
│   ├── background/    service worker：右鍵選單註冊
│   ├── popup/         擴充彈出視窗
│   └── masking.ts     遮蔽與對照表建立
├── tests/             一致性、已知分歧、延遲測試
├── public/            manifest.json、popup.html
└── build.mjs          esbuild 建置腳本
```

## 規則層與 Python 版的一致性

`src/core/` 是 `core/rules/` 的 TypeScript 移植，兩版**必須產生完全相同的輸出**。由 `tests/parity.test.ts` 用共用語料逐筆把關。

改動任一版的規則層邏輯後，都要重跑：

```bash
python tools/gen_parity_expected.py
cd extension && npm test
```

完整說明與**已知的全形數字分歧問題**見 [docs/ts_port.md](../docs/ts_port.md)。

## 佔位符配號：不使用 detect_all 的 replacement

`detect_all()` 每次呼叫都從 1 重新編號。同一個真值這次可能是 `[TW_ID_1]`、下次變 `[TW_ID_2]`，而 `[TW_ID_1]` 還可能被別的真值佔用——還原時會**把兩個人的個資對調**，比洩漏更嚴重。（問題由 B 在 PR #3 提出，proxy 端用同樣方式規避。）

擴充端由 [`src/placeholder.ts`](src/placeholder.ts) 的 `PlaceholderAllocator` 自行配號，只採用 `detect_all` 的 `type` 與座標。型別代碼一律先經過 `normalizeType()`（對應 proxy 的 `mapping.normalize_type()`），確保兩個載體產生的佔位符格式一致、還原得回去。

作用域刻意限定在**單一對話**：

- 對話**之內**要一致 → AI 才知道多輪之間的 `[NAME_1]` 是同一個人
- 對話**之間**不該一致 → 穩定假名跨情境流通等於自己造了追蹤識別子；對照表是明文個資、是攻擊面，不該無限增長

配號狀態以對話網址為 key 存進 `chrome.storage.local`，12 小時後過期。

## 目前進度

- [x] 規則層 TypeScript 移植（8 個偵測器 + Layer 4 衝突解析）
- [x] Python ↔ TypeScript 一致性測試
- [x] MV3 擴充骨架與建置流程
- [x] paste 攔截 + 確認面板（Shadow DOM 隔離）
- [x] textarea / contenteditable 文字插入
- [x] Popup 即時偵測介面
- [x] 佔位符配號器（跨次貼上維持同值同碼）
- [ ] 語意層（transformers.js + NER 模型）—— 技術風險高，見下
- [ ] 還原機制（第二版，佔位符 + 對照表）

## 語意層的技術風險（R1 / R2）

### 延遲：這是目前最大的問題

| 來源 | 情境 | 耗時 |
|---|---|---|
| 規則層（本專案實測） | 2 千字 | 0.11 ms |
| 規則層（本專案實測） | 2 萬字 | 2.33 ms |
| **NER 模型**（D 於 PR #3 實測，PyTorch CPU） | **2800 字** | **mean 821.7 ms / max 1153.4 ms** |

規則層對 1 秒預算的佔用可以忽略，**延遲風險 100% 在語意層**。

而 D 量的是 **PyTorch 原生 CPU**。瀏覽器裡跑 transformers.js（WASM）通常比原生 CPU 更慢，也就是說同一個模型在擴充內對 2800 字很可能要**數秒**——直接撞破 R2 的 1 秒門檻。

這代表「貼上時同步跑完整 NER 再顯示面板」大概行不通。可能的方向（尚未決定，待討論）：

1. **分段顯示**：規則層結果先秒出（0.11 ms），面板先開，語意層算完再補進來。體驗最好，但面板要能中途更新。
2. **換更小的模型**：distil / 量化到 int8，犧牲一些 recall 換延遲。
3. **限縮觸發條件**：只對較短的文字或使用者主動要求時才跑語意層。
4. **走專題報告的既定退路**：擴充只保留規則層 + 警告（放棄順序第 1 項）。

### 另外三個已知地雷

1. **模型格式**：`gyr66/bert-base-chinese-finetuned-ner` 是 PyTorch 權重，transformers.js 只吃 ONNX，必須先用 `optimum` 轉檔並量化。這件事沒有排進原時程。
2. **不要放進 service worker**：MV3 的 service worker 閒置約 30 秒就被 Chrome 終止，每次喚醒都要重新載入模型。應該用 `chrome.offscreen` 開 offscreen document（有完整 DOM、生命週期可控）。
3. **CSP**：MV3 允許 `wasm-unsafe-eval`，但需要在 manifest 明確宣告；模型檔建議打包進擴充而非從 CDN 下載，才守得住「不經第三方伺服器」的訴求。
