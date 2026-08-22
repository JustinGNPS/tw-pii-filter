# API Proxy（載體二：AI coding agent 防護）

攔截 AI coding agent 送往雲端 LLM 的請求，在本地完成 PII 偵測、遮蔽與還原。

```
Agent（Aider / Cline / Continue / Codex / OpenCode）
      │ 客戶 A123456789
      ↓ 以為自己在打 OpenAI
  本 proxy（localhost:8000）
      │ ① core.rules.detect_all() 找出個資
      │ ② 換成佔位符，對照表記在記憶體裡
      ↓ 客戶 [TW_ID_1]
   上游 LLM（長庚 AIR）      ← 雲端從頭到尾看不到真值
      │ ...[TW_ID_1]...
      ↓
  本 proxy  ③ 查對照表換回真值（含 SSE 串流重組）
      ↓ ...A123456789...
   Agent                    ← 與沒裝過濾器時完全一致
```

## 目前版本：遮蔽 + 還原

**遮蔽與還原必須同時啟用。** 只遮蔽不還原會讓 agent 的 diff 比對失敗
—— 實測 Aider 會回報 `SEARCH/REPLACE block failed to match!`，因為 AI 依
「已遮蔽」的內容產生 SEARCH 區塊，Aider 拿去比對硬碟上「未遮蔽」的檔案。

### 對照表只存記憶體

真實個資**不寫入任何檔案**，proxy 行程結束即消失。不產生的東西不可能外洩，
也就不需要處理加密、權限與刪除時機。

### 閒置逾時自動清空對照表

對照表存的是明文個資，不該無限期駐留——閒置超過 `PROXY_MAPPING_IDLE_TIMEOUT`
秒（預設 1800 秒／30 分鐘）沒有新的遮蔽動作，下一次遮蔽前會整張清空重來。

只在遮蔽（`token_for()`）時檢查，**不在還原時檢查**：還原永遠緊接在同一次
請求的遮蔽之後，遮蔽當下沒清空、還原就不會撲空；若還原也做這個檢查，長時間
串流中的還原可能被自己觸發的清空打斷。清空是安全的——AI 的回覆在離開 proxy
前就已經把佔位符還原成真值，agent 端保存的對話歷史從頭到尾都是真值，不會有
「佔位符指向已清空的對照」這種情況，唯一的代價是佔位符數字重新從 1 開始。

### 號碼由 proxy 自己發

不直接採用 A 的 `replacement` 欄位。A 每次都從 1 重新編號，而 agent 每次請求
都會重送整段對話歷史，同一真值可能在不同次請求拿到不同號碼、號碼也可能被別的
真值佔用 —— 還原時會把兩個人的個資對調。因此**一個真值第一次出現時配一個號碼，
之後永遠是那個號碼**。

### 型別代碼一律正規化成大寫

`mapping.normalize_type()` 會把任何型別代碼轉成 `[A-Z][A-Z_]*` 的形式
（`name` → `NAME`）。**只做格式正規化，不做語意改名** —— 不會自作主張把
`name` 改成 `PERSON`，對外的類別代碼叫什麼是 `docs/interface.md` 的決定。

為什麼需要：語意層（D 的 NER）回傳的是模型的 `entity_group`，實測是小寫的
`name` / `address` / `position`。若原樣拿去發號碼會產生 `[name_1]`，而還原用的
`TOKEN_PATTERN` 只認大寫 —— **遮蔽成功、還原失效**，佔位符會被寫進使用者的檔案。

正則不放寬成接受小寫，是因為 `[abc_1]` 這種寫法在程式碼裡很常見，放寬會讓還原
去動到不該動的東西。**把入口收乾淨，比把出口放寬安全。**

順帶解掉一個撞號風險：`name` 與 `NAME` 若被當成兩個型別，兩邊都從 1 號開始發，
會產生兩個 `[NAME_1]` 指向不同的人。

### 兩個型別機制：「不採信」與「不遮蔽」不是同一件事

語意層的輸出經過**兩道**互相獨立的型別篩選，兩者意義不同，設定時不要混用：

| | `PII_NER_ALLOW_TYPES`（白名單） | `PII_SKIP_TYPES`（跳過清單） |
|---|---|---|
| 意思 | 這個型別**根本不可信**，當作沒偵測到 | 偵測是對的，但**政策上不遮** |
| 進 Layer 4 仲裁 | ❌ 送進 `detect_all()` 之前就丟掉 | ✅ |
| 計入 log 筆數 | ❌ | ✅ |
| 計入組合風險分數 | ❌ | ✅ |
| 預設值 | `NAME,ADDRESS,POSITION,COMPANY` | `POSITION,COMPANY` |

**`POSITION` / `COMPANY` 走的是第二種**：職稱、公司名不是個人識別資料，遮掉
對隱私沒有幫助，卻會讓 agent 讀不懂上下文（程式碼裡的類別名稱、套件名稱常
含公司名）。但它們**仍然要計入組合風險分數** —— AI 真的看得到這段文字，
它對重新識別的貢獻是真實的。這也是為什麼它們留在白名單裡：若把它們排除在
白名單外，風險分數會漏報。

**雜訊型別走的是第一種**，理由見下一節。

### 還原涵蓋文字回覆與工具呼叫兩種路徑

AI 有兩種方式把「要怎麼改檔案」告訴 agent：**純文字回覆**（`delta.content`，
Aider 這類 diff-edit 工具走這條）或 **function calling**（`delta.tool_calls[]
.function.arguments`，OpenCode 這類用內建編輯工具的 agent 走這條）。兩條路徑
都會被 `SSERestorer` 還原；同一回覆裡可能同時有多個工具呼叫在串流，各自
用 `index` 維護獨立的 buffer，避免內容互相插斷拼錯。

⚠️ **這是實測跑出來的教訓，不是預先設計好的**：第一版只處理了
`delta.content`，用 OpenCode 端到端測試時發現使用者的檔案被寫進
`[TW_ID_1]` 這類佔位符——因為 OpenCode 用 function calling 編輯檔案，
內容根本沒經過當時唯一會檢查的那個欄位。log 當下顯示「還原 0 筆」，
這個訊號本身就是破案關鍵。詳見 `docs/B_design.md`「已知限制」一節。

### 兩種 API 格式：chat completions 與 Responses API

| agent | 端點 | 請求裡的文字在哪 | 串流事件 |
|---|---|---|---|
| Aider／OpenCode／Continue／Cline | `/v1/chat/completions` | `messages[]` | `choices[].delta.content`、`delta.tool_calls[].function.arguments` |
| **Codex** | `/v1/responses` | 頂層 `instructions` + `input[]` **物件陣列**（`message` / `function_call` / `function_call_output`） | 頂層 `{"type": "response.*", "delta": ...}` |
| Claude Code | `/v1/messages` | `system` + `messages[]` block 陣列 | Anthropic 事件（另有 `proxy/anthropic_adapter.py` 翻譯層） |

Responses API 不需要協定翻譯（上游 AIR 直接支援），但**遮蔽與還原兩側都要
另外認得它的欄位位置**——`extract_texts()` 與 `SSERestorer` 各自分流處理。
agent 讀到的檔案內容在 `input[].output`，agent 要寫進檔案的內容在
`response.function_call_arguments.delta`，這兩個是最關鍵的欄位。

### 語意層（D 的 NER）：預設關閉，選配開啟

`proxy/detector.py` 已接上 `core.ner.detect_ner()`，會把語意層結果當
`extra_spans` 一起送進 `detect_all()` 仲裁。**規則層與語意層各自獨立掃描
同一段文字，只在最後由 Layer 4 合併、仲裁重疊** —— 規則層的 8 種型別不會
經過語意層判斷，反之亦然。

預設**關閉**（`PII_ENABLE_NER` 沒設定或設為空），只跑規則層。原因：

- 語意層單次推論（CPU）約 2412 ms，是規則層（2〜4 ms）的六百多倍，
  且是同步阻塞呼叫，開著會讓每個請求都平白多付這筆延遲。這個數字是
  D 修好 512 token 靜默截斷的 bug 後重新量測的結果（原本的 742 ms
  是「沒掃完整份文字」測出來的假快，已作廢，見 PR #15）
- `core.ner.detector` 內部會 `import torch` / `transformers`，這兩個套件
  很重（GB 等級 + 模型權重）。關閉時 proxy 完全不 import 這個模組，
  不需要安裝 `core/ner/requirements.txt` 也能跑

要展示姓名/地址遮蔽效果時，設 `PII_ENABLE_NER=1`（先 `pip install -r
core/ner/requirements.txt`）。遮蔽本身（`_mask_request`）已經用
`asyncio.to_thread` 丟到背景執行緒跑，開啟語意層後也不會卡住 event loop
上其他請求。

### 組合風險提示（D 的 Layer 3）：只提示，不遮蔽

遮蔽擋得住「字串本身就是識別碼」的東西，擋不住「沒有一個欄位是個資、組合
起來卻指得到人」。例如
`這位 35 歲的女性住在新竹市東區，是我們公司的資深後端工程師` —— 地址遮掉
之後仍留下年齡、性別、職稱，範圍已經窄到只剩少數人。

proxy 遮蔽時會順手呼叫 `core.risk.combination_risk` 評分，超過門檻就印一行：

```
WARNING 組合風險 0.70（高）：AGE、GENDER、POSITION 同時出現，即使明碼個資已遮蔽，仍可能指認到特定個人
    · 「35歲」建議泛化為「35-39歲」
    · 若非必要，建議省略性別資訊
    · 職稱可保留，但建議避免同時透露服務公司名稱
```

**它只印 log，不會改動送出去的內容，也不會擋下請求** —— 要遮到什麼程度取決於
使用者當下在做什麼任務，proxy 判斷不了（設計理由見 `docs/B_design.md` 決定 12）。

評分依據是**遮蔽後**的內容，不是原文：已經被遮掉的型別不算殘餘風險，否則會
虛報一個自己已經擋掉的洩漏。

⚠️ **需要 `PII_ENABLE_NER=1` 才看得到效果**。準識別子裡 `ADDRESS`／`POSITION`／
`COMPANY` 只有語意層抓得到，規則層那 8 個型別全都是直接識別碼、不算準識別子。
語意層關閉時可貢獻分數的只剩 `AGE` + `GENDER` = 0.50，碰不到 0.6 的警告門檻。

⚠️ **開啟語意層後，proxy 啟動後的第一個請求會等約 13 秒**（真實 Claude Code
請求實測）。原因是 agent 的 system prompt 有兩、三萬字元，語意層推論成本正比
於文字長度（約 0.36 ms／字元）。這是**一次性**的 —— 第 2 輪起 system prompt
命中偵測快取，只剩 30〜50 ms，之後的成本只正比於新增內容。**demo 前先隨便送
一個請求把快取暖起來**，否則第一次操作看起來像卡住。詳細數字見
`docs/B_design.md`「已知限制 3」。

不需要時用 `PII_ENABLE_RISK_WARNING=0` 關閉。

### ⚠️ 語意層只採信白名單裡的型別

語意層用的 `gyr66/bert-base-chinese-finetuned-ner` 是**通用領域**中文 NER
模型，訓練標籤共 **14 種**（`python core/ner/get_model_labels.py` 可查）：

```
QQ, address, book, company, email, game, government,
mobile, movie, name, organization, position, scene, vx
```

其中大半跟個資無關（書名、電影、遊戲、場景、QQ／微信帳號）。而 agent 送來的
內容大量是**英文技術文字**（system prompt、程式碼、工具說明），落在模型訓練
分布之外，產出的幾乎全是雜訊。用真實 Claude Code 請求（36,428 字元）實測，
語意層在 Claude Code 自己的 system prompt 上判出：

```
GAME         x6   'here'  'ollama'  '—'  'MEMORY'  '`'  'Environment\nYou'
ORGANIZATION x2   'mistral'  '`'
```

**一個反引號被判成 `GAME`、遮成佔位符送給上游。** 這不是隱私問題（那些本來
就不是個資），是**功能損害**：agent 的系統提示被挖洞，行為會受影響；同時
log 的「偵測到 N 筆敏感資訊」也被灌水，使用者無從得知其中幾筆是真的。

因此語意層改用**白名單**（`PII_NER_ALLOW_TYPES`，預設
`NAME,ADDRESS,POSITION,COMPANY`），白名單外的型別直接丟棄。用白名單而不是
把雜訊列進 `PII_SKIP_TYPES` 黑名單，是因為黑名單要求我們預先知道模型的所有
標籤 —— 而這個專案有 11 天都以為只有 4 種，直到 08-14 才去核對模型本身。
白名單在日後換模型時不會被新標籤偷襲。

**規則層不受影響**：那 8 種型別有正則與檢核碼驗證，照常偵測與遮蔽。

**已知限制：白名單擋不掉「白名單型別內部」的誤判。** 同一份實測裡，語意層
把英文字碎片 `'lly'` 判成 `NAME`（信心 0.99），這筆仍然會被遮。白名單把該次
9 筆雜訊降到 1 筆，但沒有根治 —— 根因是拿中文通用模型去掃英文技術文字，
要真正解決得換模型或加語言判斷，兩者都超出載體端的範圍。

### 已知取捨

同一真值永遠對到同一佔位符，因此雲端 AI 可以看出「這兩處是同一個人」
（關聯性洩漏），但看不到真實身分。若每次都換不同佔位符，agent 的 diff
比對就會失敗，因此一致性是必要的。

## 環境需求

| 項目 | 版本 |
|---|---|
| Python | 3.11 |
| 相依套件 | 見 `proxy/requirements.txt`（版本鎖死） |
| 作業系統 | 不限，目前在 Windows 11 開發 |
| 網路 | 需能連到上游 LLM；proxy 本身只聽 localhost |

## 安裝

在 repo 根目錄：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r proxy/requirements.txt
```

## 環境變數

放在 repo 根目錄的 `.env`（已被 `.gitignore` 擋住，不會進 git）。
每個設定都接受多個名稱，第一個有值的獲勝：

| 設定 | 接受的變數名稱（依序） | 預設值 |
|---|---|---|
| 上游 base URL | `UPSTREAM_BASE_URL`、`OPENAI_BASE_URL`、`OPENAI_API_BASE`、`AIR_BASE_URL` | 無（**必填**，例：`https://<your-llm-gateway>/v1`） |
| 上游金鑰 | `UPSTREAM_API_KEY`、`OPENAI_API_KEY`、`AIR_API_KEY`、`API_KEY` | 無（未設定會轉發失敗） |
| 預設模型 | `DEFAULT_MODEL`、`OPENAI_MODEL`、`AIR_MODEL` | `gpt-4.1-mini` |
| 連線逾時（秒） | `PROXY_CONNECT_TIMEOUT` | `10` |
| 讀取逾時（秒） | `PROXY_READ_TIMEOUT` | `600` |
| 不遮蔽的型別（逗號分隔） | `PII_SKIP_TYPES` | `POSITION,COMPANY` |
| 語意層採信的型別（逗號分隔） | `PII_NER_ALLOW_TYPES` | `NAME,ADDRESS,POSITION,COMPANY` |
| 啟用語意層（NER） | `PII_ENABLE_NER` | 關閉（僅規則層） |
| 啟用組合風險提示 | `PII_ENABLE_RISK_WARNING` | 開啟 |
| 對照表閒置逾時（秒，`0` 代表停用） | `PROXY_MAPPING_IDLE_TIMEOUT` | `1800`（30 分鐘） |

上游 base URL 沒設定時，proxy **仍會正常啟動**（啟動摘要會印一行 WARNING），但每個轉發請求都會回 `502` 與一則指出該設哪個變數的訊息：

```json
{"error": {"message": "上游 base URL 未設定……請在 repo 根目錄的 .env 設定 UPSTREAM_BASE_URL……", "type": "proxy_configuration_error", "code": "upstream_not_configured"}}
```

刻意不在啟動時就讓行程死掉：`/healthz` 要留著可用，使用者正是要靠它看出 `upstream` 是空的。

`PII_SKIP_TYPES` 設成空字串代表**什麼都不跳過**（連職稱也遮）；
`PII_NER_ALLOW_TYPES` 設成空字串代表**語意層全部採信**（回到白名單前的行為，
供比對／除錯用）。兩者大小寫都吃，內部會做同一套正規化。啟動時會把實際生效的
清單印出來。兩個機制的差別見上面「兩個型別機制」一節 —— **不要用 `SKIP_TYPES`
去擋雜訊型別**，那樣它們仍會被算進 log 筆數與組合風險分數。

**建議用 `UPSTREAM_API_KEY`** —— `OPENAI_API_KEY` 會與 agent 自己的設定撞名。
真金鑰只存在 proxy 這一側，agent 那邊填假的即可。

## 啟動

```powershell
.venv\Scripts\python.exe -m uvicorn proxy.main:app --port 8000
```

健康檢查（不會轉發到上游）：

```powershell
curl http://localhost:8000/healthz
# {"status":"ok","mode":"masking","mapping_entries":0, ...}
```

## 讓 agent 走 proxy

把 agent 的 OpenAI base URL 指到 `http://localhost:8000/v1`。以 Aider 為例：

```powershell
$env:OPENAI_API_BASE = "http://localhost:8000/v1"
$env:OPENAI_API_KEY  = "dummy"   # 真金鑰在 proxy 那邊
aider --model gpt-4.1-mini
```

其他 agent 只需要把 base URL 指過來，**proxy 本身不用改**。
唯一例外是 **Cursor**：走自家後端，攔不到，誠實列為不支援。

## 測試

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

`tests/` 底下與 proxy 相關的測試全部用 `respx` 假造上游，
**不會打真實 API、不需要金鑰**，組員與 CI 都能直接跑。

## 檔案

| 檔案 | 職責 |
|---|---|
| `main.py` | FastAPI 應用、路由、log、串流處理 |
| `forward.py` | httpx 轉發、標頭改寫、金鑰替換 |
| `detector.py` | 包住 A 的 `detect_all()`、從 payload 挖出該掃的欄位 |
| `masker.py` | 遮蔽（**從後往前**替換，避免座標位移） |
| `restorer.py` | 還原（非串流整包替換 + SSE 逐事件重組） |
| `mapping.py` | 對照表（記憶體、雙向、自己發號碼） |
| `cache.py` | 偵測結果快取（LRU，key 為 SHA-256 指紋） |
| `config.py` | 環境變數（金鑰一律走 `os.getenv()`，不寫進 log） |

## 效能

| 項目 | 數值 |
|---|---|
| 遮蔽（2761 字元、含 40 筆個資） | 2〜4 ms |
| proxy 總額外成本 | 約 5 ms |
| 上游 LLM 本身 | 1400〜2900 ms |
| **占比** | **約 0.25%** |

以上是**只跑規則層**的數字，`PII_ENABLE_NER` 關閉時就是這張表。語意層已經
接上（見上一節），但預設關閉；D 修好 512 token 截斷問題後重新實測 CPU、
2800 字元單次推論 **median 2443 ms**（占上游往返約 84〜175%，比上游本身
還慢），這也是為什麼語意層做成可開關、預設關閉是對的決定。規則層永遠開
—— 台灣身分證／統編有 checksum，既快又是確定性的偵測，那才是本專題的核心。

### 偵測快取

agent 每次請求都重送整段對話歷史，同一份檔案內容會被重複掃十幾次。
模擬 8 輪對話（共 36 個欄位、其中只有 8 個是新的）：

| 情境 | 無快取 | 有快取 | 省下 |
|---|---|---|---|
| 純規則層 | 59.9 ms | 18.0 ms | **70%** |
| 假設 Layer 2 每次推論 200 ms | 7261 ms | 1619 ms | **78%** |

命中率隨對話變長而上升（8 輪時為 78%）。
`/healthz` 會回報即時的命中率與快取筆數。
