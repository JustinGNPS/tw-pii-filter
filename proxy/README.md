# API Proxy（載體二：AI coding agent 防護）

攔截 AI coding agent 送往雲端 LLM 的請求，在本地完成 PII 偵測。

```
Agent（Aider / Cline / Continue / Codex / OpenCode）
      ↓ 以為自己在打 OpenAI
  本 proxy（localhost:8000）
      ↓ core.rules.detect_all() 偵測
   上游 LLM（長庚 AIR）
      ↓
  本 proxy → Agent
```

## 目前版本：透明轉發 + 只警告

依 PDF §7.3，第一版**不修改請求內容**，偵測到個資時只在 proxy 的 log 印出
型別與筆數。遮蔽與還原是下一版。

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

金鑰只在 proxy 這一側；agent 那邊可以填假的。

## 啟動

```powershell
.venv\Scripts\python.exe -m uvicorn proxy.main:app --port 8000
```

健康檢查（不會轉發到上游）：

```powershell
curl http://localhost:8000/healthz
```

## 讓 agent 走 proxy

把 agent 的 OpenAI base URL 指到 `http://localhost:8000/v1`。以 Aider 為例：

```powershell
$env:OPENAI_API_BASE = "http://localhost:8000/v1"
$env:OPENAI_API_KEY  = "dummy"   # 真金鑰在 proxy 那邊
aider --model gpt-4.1-mini
```

## 測試

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

`tests/test_proxy.py` 用 `respx` 假造上游，**不會打真實 API、不需要金鑰**。

## 檔案

| 檔案 | 職責 |
|---|---|
| `main.py` | FastAPI 應用、路由、log、SSE 串流處理 |
| `forward.py` | httpx 轉發、標頭改寫 |
| `detector.py` | 包住 A 的 `detect_all()`、從 payload 挖出該掃的欄位 |
| `config.py` | 環境變數（金鑰一律走 `os.getenv()`，不寫進 log） |
