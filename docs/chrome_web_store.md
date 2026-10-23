# Chrome Web Store 上架與驗收

## 產生上架包

```powershell
.\.venv\Scripts\python.exe tools\pack_extension.py
```

產物位於 `release/tw-pii-filter-extension-v<版本>-chrome-web-store.zip`。
`manifest.json` 位於 ZIP 根目錄，可以直接上傳 Developer Dashboard。

## 本機人工驗收

1. 解壓縮上架包。
2. 開啟 `chrome://extensions`，啟用「開發人員模式」。
3. 選擇「載入未封裝項目」，指定解壓後含 `manifest.json` 的資料夾。
4. 在 ChatGPT、Claude、Gemini 各測一次：一般文字、身分證、電話、電子郵件。
5. 同時開兩個新對話分頁，確認相同 `[NAME_1]` 不會互相還原成另一分頁的人名。
6. 送出遮蔽內容後，確認 AI 頁面 DOM 仍保留佔位符；真值只出現在右下角的本機還原檢視器。
7. 等待或手動建立超過 12 小時的測試狀態，確認下次啟動會從 `chrome.storage.local` 刪除。

## 商店資料仍需人工完成

- 註冊 Chrome Web Store 開發者帳號並繳交一次性費用。
- 提供至少一張 1280×800 或 640×400 的產品截圖。
- 在可公開存取的 HTTPS 網址發布 `PRIVACY.md` 內容並填入商店欄位。
- 填寫單一用途、權限理由與資料使用揭露表。
- 上傳 ZIP、送審並等待 Google 審核。

本專案只能產生與驗證上架包；在商店正式審核通過前，無法讓一般使用者從商店一鍵安裝。
