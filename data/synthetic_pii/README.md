# 合成測試 PII 語料（Synthetic Test Data）

**⚠️ 本資料夾內所有姓名、身分證字號、地址、電話皆為程式隨機產生，非真實個資。**
僅供 `tw-pii-filter` 專案的偵測層開發、單元測試、demo 使用。

## 🔻 語料產物不在 git 裡，請先自己產生一份

**只有產生器進 git，產生出來的資料檔沒有**（`.gitignore` 已擋掉）。
第一次 clone 下來時 `synthetic_pii.csv` 等檔案不存在，跑一次下面的指令就會出現。

原因見〈身分證字號說明〉：產生出來的號碼 checksum 驗得過、格式與真號無異，
放在公開 repo 上等同散布一份可直接使用的身分證號清單，
而且不排除隨機撞到真人的號碼。產生器留著、產物不留，兩邊都顧到。

## 內容

| 檔案 / 資料夾                         | 進 git？ | 說明                                                         |
| -------------------------------------- | :---: | ------------------------------------------------------------ |
| `generate_fake_pii.py`                 | ✅ | 產生器主程式，含台灣身分證字號 checksum 演算法（格式合法但虛構） |
| `generate_fake_code_samples.py`        | ✅ | 把合成個資嵌入模擬真實情境的檔案（客戶匯出腳本、對話紀錄、API log、CRM 備註） |
| `synthetic_pii.csv` / `synthetic_pii.json` | ❌ | 80 筆純資料（姓名、身分證字號、地址、電話）                    |
| `fake_code_samples/customer_export.py` | ❌ | 模擬工程師把客戶資料匯出腳本貼給 AI agent 除錯的情境           |
| `fake_code_samples/chat_log.txt`       | ❌ | 模擬客服對話紀錄外洩到程式碼倉庫的情境                          |
| `fake_code_samples/api_request_log.json` | ❌ | 模擬 API request log 明文寫入個資的情境                       |
| `fake_code_samples/crm_notes.md`       | ❌ | 模擬業務把客戶備註貼到筆記軟體的情境                            |

## 重新產生 / 調整筆數

在 **repo 根目錄**執行：

```bash
python data/synthetic_pii/generate_fake_pii.py --count 80 --seed 42 --out-dir data/synthetic_pii/output
python data/synthetic_pii/generate_fake_code_samples.py
```

兩支程式都輸出到 `data/synthetic_pii/output/`，最後把產物搬上一層到
`data/synthetic_pii/` 底下（`core/ner/` 的評估腳本是照這個路徑找檔案的）：

```bash
# PowerShell
Move-Item data\synthetic_pii\output\* data\synthetic_pii\ -Force
Remove-Item data\synthetic_pii\output -Recurse -Force

# bash
mv data/synthetic_pii/output/* data/synthetic_pii/ && rmdir data/synthetic_pii/output
```

- `--count`：要產生幾筆純資料
- `--seed`：固定亂數種子，方便重現同一批測試資料
- **用預設的 `--count 80 --seed 42` 產出的檔案與專案原本使用的語料 bit-for-bit 相同**，
  所以 `core/ner/eval_precision_recall.py` 裡那份寫死的切片對照表不會失效。
  換了 count 或 seed 就要同步更新那份對照表。

## 身分證字號說明

`generate_fake_pii.py` 內建官方公開的身分證字號 checksum 演算法，
產生出來的號碼**格式合法、驗證碼算得過**，但號碼本身是隨機組合，
沒有查證是否曾核發給真人。如果之後合規要求更嚴格，可以再加一層
「已知測試號碼黑名單排除」或改用官方公告的保留測試號段。

## 放置位置建議

依照 `CONTRIBUTING.md` 的專案結構規劃，建議整包放在：

```
tw-pii-filter/
└── data/
    └── synthetic_pii/   ← 本資料夾內容放這裡
```
