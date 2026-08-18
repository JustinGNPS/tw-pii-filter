# CLAUDE.md

> 這個檔案會在 Claude Code 進入本 repo 時自動載入，全組共用。
> 「B 的工作方式」那一節是 B 用 Claude Code 開發時的個人流程約定，其他人看到可以略過，
> 但**上面的規則對全部人都適用**。

## 測試

- 一律用 `python -m pytest tests/` 執行測試，**不要用 `python -m unittest discover`**。
  本專案的測試檔案混用了 `unittest.TestCase` 風格與純 pytest 風格（`test_proxy.py`、
  `test_mapping.py`、`test_masker.py`、`test_restorer.py`、`test_sse_restorer.py`），
  `unittest discover` 會**靜默跳過**純 pytest 風格的測試（不報錯、也不計入總數），
  容易誤以為測試都跑了、其實漏了一大批。

---

## B 的工作方式（B：Agent 防護 / API Proxy，載體二）

> 使用者是 **B**，負責載體二：AI coding agent 防護（LLM API Proxy）+ 遮蔽與還原機制。

### 每次開工必做（不要等使用者提醒）

1. **`git fetch --all --prune`**，然後看：
   - 組員有沒有推新 commit / 新分支
   - 有沒有等著被 review 的 PR
2. **讀 `docs/B_progress.md`**（本機檔案，不進 git）—— 上次做到哪、下一步、卡在什麼、待組員回覆什麼
3. **若 `docs/interface.md` 有變動**，先檢查 `proxy/detector.py` 是否要跟著改（介面變動只該影響這一個檔案）
4. **跟使用者報告**：組員有什麼新東西 → 上次進度 → 今天建議做什麼
5. **收工前更新 `docs/B_progress.md`**，包含做了什麼、為什麼這樣做、遇到什麼問題（使用者要拿這份跟組員與教授說明）

### 工作方式（使用者明確要求）

- **動手前先討論。** 建分支、寫程式檔、改設定、push 這類會留痕跡的動作，先用文字說明「要做什麼、為什麼、會產生哪些檔案」，等使用者點頭再執行。
- 純讀取（Read / git log / 查文件）可以直接做，做完講一聲即可。
- 使用者正在學習中，解釋要具體，不要假設他知道工具名詞的意思。
- **這台機器的 C 槽空間有限。** 這個專案開發過程中需要下載/安裝的東西（pip 套件快取、
  HuggingFace 模型權重等）一律存到 D 槽，不要放 C 槽。pip 全域快取與 `HF_HOME` 已經
  永久指到 `D:\專題(new)\.pip_cache` / `D:\專題(new)\.hf_cache`。

---

## 專案是什麼

本地端敏感資訊過濾機制 —— 資料送往雲端 AI 之前，在本地完成 PII 偵測與可逆處理，AI 回覆時自動還原。

核心賣點是實驗證明的缺口：Presidio / GLiNER / LLM Guard 對**台灣身分證、統編的 recall 是 0.000**，且沒有任何工具驗證檢核碼。

**一個核心，兩個載體**：偵測核心（A + D）共用；載體一是瀏覽器擴充（C），載體二是 agent 防護（B）。

完整規劃見 `D:\專題(new)\隱私專題PRO MAX.pdf`（20 頁），B 的工作清單見 `D:\專題(new)\work_B.md`。

## B 的職責範圍

```
Agent（Aider/Cline…）
      ↓ 送出請求
  B 的 API Proxy ← 攔截
      ↓ 呼叫 core.rules.detect_all() 找出個資（辨識是 A/D 的事，不是 B）
      ↓ 遮蔽後轉發
   雲端 AI
      ↓ 回覆
  B 的 Proxy ← 還原
      ↓ 把 [TW_ID_1] 換回真值
   使用者看到原文
```

**A/D 負責「認得出來」，B 負責「真的攔得下來、換得掉、還原得回去」。** B 寫的程式裡沒有任何 AI，只有協定處理與字串操作。

B 要解決的工程問題：協定相容（騙過 agent）、從 payload 挖出該掃的欄位、替換後的座標位移、對照表管理、SSE 串流還原、五款 agent 相容、延遲控制。

**關鍵架構決定**：攔截點是 **LLM API 層，不是 MCP**（六款 agent 讀檔都走內建工具）。Aider / Continue / Cline / Codex / OpenCode 五款可攔；**Cursor 走自家後端攔不到，誠實列為不支援**。

## 依賴關係

```
A ──提供 core.rules.detect_all()──→ B    ← B 等 A，A 不等 B
D ──提供 core.ner.detect_ner()────→ B    ← 語意層，PII_ENABLE_NER 開關控制
B ──提供還原邏輯──────────────────→ C    ← C 有自己獨立的 TS 實作，非直接呼叫
D ──提供假專案/語料───────────────→ B    ← 只在 demo 與測試時需要
```

---

## 介面契約（`docs/interface.md`）

```python
detect_all(text: str, extra_spans: list = None) -> dict
# {"text": str,
#  "spans": [{start, end, type, text, confidence, source, replacement}, ...]}
```

- `end` 為 Python slice 慣例（不含），`text[start:end]` 即為 span 的 `text`
- `replacement` 格式 `[TYPE_N]`，同類別序號從 1 遞增
- `extra_spans`：語意層（D 的 NER）結果，未經整合前的原始 spans
- **`detect_all` 回傳的 spans 保證互不重疊**（Layer 4 仲裁已由 A 完成，`core/rules/conflict_resolver.py`）
- 型別代碼：`TW_ID` / `TW_TAX` / `TW_NHI` / `TW_PHONE_M` / `TW_PHONE_L` / `EMAIL` / `CREDIT_CARD` / `API_KEY`（規則層）+ `NAME` / `ADDRESS` / `POSITION` / `COMPANY`（語意層，已統一轉大寫）

**兩個實作上的坑**：
1. **從後往前替換**（座標由大到小），否則前面替換造成的長度變化會讓後面的座標失效
2. **型別代碼大小寫**：語意層模型原生輸出過小寫代碼，`proxy/mapping.normalize_type()` 有做防禦性正規化，但不要假設下游一定是大寫

---

## 環境

- Python 3.11（`.venv/` 在 repo 根目錄，已被 `.gitignore` 擋掉）
- 上游 LLM：OpenAI 相容端點，base URL 由 `.env` 的 `UPSTREAM_BASE_URL` 指定（程式沒有預設值），測試預設模型 `gpt-4.1-mini`
- 金鑰與設定在 repo 根目錄 `.env`（`.gitignore` 第 2 行已擋）
- 語意層（`PII_ENABLE_NER=1`）需要另外 `pip install -r core/ner/requirements.txt`（torch/transformers，體積大，預設不裝）

> **`.env` 絕對不要讀取、不要印出、不要複製到任何其他檔案。** 需要用時一律透過 `os.getenv()`。

## 團隊鐵則（`CONTRIBUTING.md`）

1. **禁止直接改 `main`** —— 一律 branch + Pull Request
2. 每天開工先 `git pull`
3. **絕不 commit 個資** —— `.env`、對照表、真實測試資料
4. commit 訊息寫清楚（「修 bug」不行，要「修正統編第 7 碼特例」）
5. 一個功能一個 branch
6. **套件版本鎖死，不擅自升級**（團隊踩過降級改變實驗結果的坑）
7. **所有檔案讀寫明確指定 `encoding="utf-8"`**（團隊踩過 cp950 的坑，且開發環境是 Windows）

B 的分支：`feature/detect-cache`（偵測快取 + 語意層接線）
