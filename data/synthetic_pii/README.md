# 合成測試 PII 語料（Synthetic Test Data）

**⚠️ 本資料夾內所有姓名、身分證字號、地址、電話皆為程式隨機產生，非真實個資。**
僅供 `tw-pii-filter` 專案的偵測層開發、單元測試、demo 使用。

## 內容

| 檔案 / 資料夾                         | 說明                                                         |
| -------------------------------------- | ------------------------------------------------------------ |
| `generate_fake_pii.py`                 | 產生器主程式，含台灣身分證字號 checksum 演算法（格式合法但虛構） |
| `generate_fake_code_samples.py`        | 把合成個資嵌入模擬真實情境的檔案（客戶匯出腳本、對話紀錄、API log、CRM 備註） |
| `synthetic_pii.csv` / `synthetic_pii.json` | 80 筆純資料（姓名、身分證字號、地址、電話）                    |
| `fake_code_samples/customer_export.py` | 模擬工程師把客戶資料匯出腳本貼給 AI agent 除錯的情境           |
| `fake_code_samples/chat_log.txt`       | 模擬客服對話紀錄外洩到程式碼倉庫的情境                          |
| `fake_code_samples/api_request_log.json` | 模擬 API request log 明文寫入個資的情境                       |
| `fake_code_samples/crm_notes.md`       | 模擬業務把客戶備註貼到筆記軟體的情境                            |

## 重新產生 / 調整筆數

```bash
python generate_fake_pii.py --count 80 --seed 42 --out-dir .
python generate_fake_code_samples.py
```

- `--count`：要產生幾筆純資料
- `--seed`：固定亂數種子，方便重現同一批測試資料

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
