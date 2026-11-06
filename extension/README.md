# 瀏覽器擴充（載體一）

貼上文字到 AI 網頁之前，於本地端偵測台灣個資並提示遮蔽。規則層直接在瀏覽器執行；使用者可選擇把文字送到同一台電腦的 `127.0.0.1` Proxy 做語意辨識。原文不會送到本專題團隊或其他過濾伺服器。

## 快速開始

```bash
cd extension
npm install
npm run build
```

到 `chrome://extensions` → 開啟右上角「開發人員模式」→「載入未封裝項目」→ 選 `extension/dist`。

要產生可直接上傳 Chrome Web Store 的 ZIP：

```bash
python tools/pack_extension.py
```

詳見 [Chrome Web Store 上架與驗收](../docs/chrome_web_store.md)。

## 兩種使用方式

**1. Popup（最快的 demo 方式）**

點瀏覽器工具列的擴充圖示，貼上文字即時看到偵測結果、遮蔽後內容與耗時。不需要開任何 AI 網站。

**2. 在 AI 網頁上自動攔截**

到 ChatGPT / Claude / Gemini，在輸入框按 Ctrl+V 貼上含個資的文字，會跳出確認面板。也可以選取輸入框裡的文字後按右鍵，選「🛡 隱私處理後貼上」。

## 開啟姓名／地址／公司／職稱辨識

語意層沿用 Proxy 已驗證的本機模型，不把數百 MB 權重塞進擴充。操作方式：

1. 雙擊根目錄 `啟動.bat`，選 `[1] 啟動 Proxy + 示範頁`。
2. 被詢問時選擇開啟語意層，等待終端顯示 Proxy 已就緒。
3. 點擴充圖示，展開「要攔截哪些個資」。
4. 勾選「使用本機 Proxy 辨識姓名、地址、公司與職稱」。
5. Popup 按「掃描」，或直接到支援的 AI 網頁貼上文字。

擴充只呼叫固定端點 `http://127.0.0.1:8000/extension/ner`。Proxy 沒啟動、未開語意層、回應逾時或回傳不可信座標時，擴充不會把「失敗」冒充成「零筆」；貼上確認面板會明確警告，讓使用者取消或自行選擇只用規則結果。

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
│   ├── semantic.ts    localhost 語意協定、座標轉換與回應驗證
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

擴充端由 [`src/placeholder.ts`](src/placeholder.ts) 的 `PlaceholderAllocator` 自行配號，只採用 `detect_all` 的 `type` 與座標。型別代碼一律先經過 `normalizeType()`（對應 `core/redact/mapping.normalize_type()`），確保兩個載體產生的佔位符格式一致、還原得回去。

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
- [x] Layer 3 組合風險評分（TypeScript 移植 + 面板提示）
- [x] 語意層型別白名單（`NER_ALLOW_TYPES`，與 proxy 一致）
- [x] 語意層（選用 localhost Proxy，辨識 NAME / ADDRESS / POSITION / COMPANY）
- [ ] 完全獨立的瀏覽器內 ONNX 語意模型（未採用；見下）
- [x] AI 回覆安全檢視（AI 網頁 DOM 保留佔位符，真值只在 closed Shadow DOM 顯示；見 `src/restore.ts`、`src/content/restorer.ts`）

## Layer 3：組合風險評分

逐項清單天生看不出「單項都不算個資、組合起來能指認個人」這件事。使用者貼一段沒有身分證也沒有電話的自我介紹，面板若只顯示「未偵測到敏感資訊」就是**誤導**——這正是專題報告 4.3 引的 llm-redactor 論文說的問題（隱含身分洩漏率 95%）。

因此擴充在兩個地方處理：

1. **`detectAll()` 回傳 `combination_risk`**（移植自 `core/risk/combination_risk.py`，行為與 Python 版一致，由 parity 測試把關）
2. **一個 span 都沒有、但有組合風險時仍會跳面板**，標題改成「這段文字可能可以指認出特定個人」，並列出貢獻的類別與泛化建議

面板不只顯示分數。只給一個 `0.82` 使用者不知道要做什麼，所以一併顯示是哪些類別造成的、以及每個類別的具體泛化建議（例如「「35歲」建議泛化為「35-39歲」」）。

### ⚠️ 已知問題：Python 版的年齡換算會隨系統日期漂移

`compute_combination_risk()` 換算民國年次/西元年生時直接呼叫 `date.today()`，沒有開放注入。「民國78年次」在 2026 年產生「37歲」建議，2027 年變成「38歲」——**任何含這類寫法的快照測試都是時間炸彈**，跨年那天會全組一起紅，而且錯誤訊息完全看不出跟日期有關。

TypeScript 版已支援注入（`extractAge(text, today)`），`tests/fixtures/parity_cases.json` 也刻意不放這類語料。已在 PR 中建議 Python 版比照辦理。

## 語意層型別白名單

`NER_ALLOW_TYPES` 對應 proxy 的 `config.NER_ALLOW_TYPES`，預設同為 `NAME` / `ADDRESS` / `POSITION` / `COMPANY`。**不在白名單裡的語意層 span 當作沒偵測到**，掛在 Layer 4 仲裁的上游。

這與「偵測到但預設不勾選」是兩個不同機制：

| | 白名單 | 預設不勾選 |
|---|---|---|
| 意思 | 根本不可信，當作沒偵測到 | 偵測對，但預設不遮 |
| 進 Layer 3 組合風險分數 | ❌ | ✅ |
| 算進「偵測到 N 項」 | ❌ | ✅ |

`COMPANY` 正是需要區分兩者的例子：留在白名單內（權重 0.15 必須計入組合風險，排除會漏報），但在面板上可以預設不勾選。

## 語意層實作與技術邊界（R1 / R2）

### 延遲：這是目前最大的問題

| 來源 | 情境 | 耗時 |
|---|---|---|
| 規則層（本專案實測） | 2 千字 | 0.11 ms |
| 規則層（本專案實測） | 2 萬字 | 2.33 ms |
| **NER 模型**（D 於 PR #3 實測，PyTorch CPU） | **2800 字** | **mean 821.7 ms / max 1153.4 ms** |

規則層對 1 秒預算的佔用可以忽略，**延遲風險 100% 在語意層**。

瀏覽器內直接跑同一模型還需要 ONNX 轉檔、量化、offscreen document 與 WASM CSP，模型大小與長文延遲也會增加 Chrome Web Store 上架風險。因此 v0.2.0 採用**本機 companion Proxy**：

- 模型仍只在使用者電腦上執行，請求不會進上游 AI。
- 擴充預設不啟用語意層，必須由使用者明確勾選。
- Popup 只有按「掃描」才呼叫模型；輸入過程仍以規則層即時預覽，避免每打一個字就推論。
- AI 網頁貼上時會顯示本機分析進度，成功後再由既有 Layer 4 合併。
- Python 使用 Unicode code-point 座標，JavaScript 使用 UTF-16；擴充會明確轉換並驗證，包含 Emoji 在前方的情況。
- 文字上限為 20,000 字，連線與格式錯誤會顯示警告，不靜默放行成「語意零筆」。

代價是進階語意功能需要同時執行 Proxy。完全獨立的瀏覽器 ONNX 版本仍可作為後續研究，但不是目前已完成或上架包內含的功能。
