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

### 號碼由 proxy 自己發

不直接採用 A 的 `replacement` 欄位。A 每次都從 1 重新編號，而 agent 每次請求
都會重送整段對話歷史，同一真值可能在不同次請求拿到不同號碼、號碼也可能被別的
真值佔用 —— 還原時會把兩個人的個資對調。因此**一個真值第一次出現時配一個號碼，
之後永遠是那個號碼**。

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
| 上游 base URL | `UPSTREAM_BASE_URL`、`OPENAI_BASE_URL`、`OPENAI_API_BASE`、`AIR_BASE_URL` | `https://air.cgu.edu.tw/cgullmapi/v1` |
| 上游金鑰 | `UPSTREAM_API_KEY`、`OPENAI_API_KEY`、`AIR_API_KEY`、`API_KEY` | 無（未設定會轉發失敗） |
| 預設模型 | `DEFAULT_MODEL`、`OPENAI_MODEL`、`AIR_MODEL` | `gpt-4.1-mini` |
| 連線逾時（秒） | `PROXY_CONNECT_TIMEOUT` | `10` |
| 讀取逾時（秒） | `PROXY_READ_TIMEOUT` | `600` |

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

> 以上為**僅規則層**的數字。Layer 2（NER 模型）接進來後會改寫，
> 屆時請重新量測。

### 偵測快取

agent 每次請求都重送整段對話歷史，同一份檔案內容會被重複掃十幾次。
模擬 8 輪對話（共 36 個欄位、其中只有 8 個是新的）：

| 情境 | 無快取 | 有快取 | 省下 |
|---|---|---|---|
| 純規則層 | 59.9 ms | 18.0 ms | **70%** |
| 假設 Layer 2 每次推論 200 ms | 7261 ms | 1619 ms | **78%** |

命中率隨對話變長而上升（8 輪時為 78%）。
`/healthz` 會回報即時的命中率與快取筆數。
