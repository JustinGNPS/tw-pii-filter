# 團隊協作規範

> 四人專題共用。第一週全員讀過並同意，之後照這個做。

---

## 一、鐵則（違反會害到別人）

1. **禁止直接改 main 分支** —— 一定走 branch + Pull Request
2. **每天開工前先 `git pull`** —— 拿別人的最新版，避免衝突
3. **絕對不要 commit 個資** —— `.env`、對照表、真實測試資料都不行（`.gitignore` 已擋，但仍要小心）
4. **commit 訊息寫清楚** —— 「修 bug」不行，要「修正統編第 7 碼特例」
5. **一個功能一個 branch** —— 不要一個 branch 塞很多功能
6. **套件版本鎖死** —— 不要擅自升級套件（我們踩過降級改變結果的坑）

---

## 二、分支命名

每個人用自己的前綴，避免混亂：

```
feature/checksum      ← A 規則層
feature/api-proxy     ← B agent 防護
feature/extension     ← C 瀏覽器擴充
feature/ner           ← D 語意層
fix/描述              ← 修 bug 時
docs/描述             ← 改文件時
```

---

## 三、每天的工作流程

```bash
# 1. 開工前，先拉最新版
git pull

# 2. 開自己的分支
git checkout -b feature/我的功能

# 3. ...改程式碼...

# 4. 看改了什麼
git status

# 5. 加入改動
git add .

# 6. 提交（訊息寫清楚）
git commit -m "實作身分證 checksum 驗證"

# 7. 推到 GitHub
git push -u origin feature/我的功能
```

推上去後，到 GitHub 網頁開 **Pull Request**，請至少一位組員看過再合併。

---

## 四、commit 訊息怎麼寫

**好的例子**
- `實作身分證字號 checksum 驗證`
- `修正統一編號第 7 碼為 7 的特例`
- `新增確認面板的逐項勾選功能`

**不好的例子**
- `修改`
- `update`
- `fix bug`

原則：讓別人一看就知道你改了什麼。

---

## 五、遇到衝突（conflict）怎麼辦

當兩人改到同一個檔案同一行，合併時會衝突。

1. **預防**：分工切乾淨，各改各的檔案，就很少衝突
2. **發生時**：Git 會在檔案裡標出衝突區塊（`<<<<<<<` 和 `>>>>>>>`），兩人一起決定留哪個版本，刪掉標記
3. **卡住就問**：把畫面貼給 Claude Code，它會教你解

---

## 六、每週開會前

- 各自把手上的 branch push 上去
- 檢查有沒有 Pull Request 等著被 review
- 同步進度與卡點

---

## 七、專案結構（建議）

```
tw-pii-filter/
├── README.md
├── CONTRIBUTING.md        ← 本檔
├── .gitignore
├── core/                  ← A + D：偵測核心
│   ├── rules/            ← A：checksum
│   └── ner/             ← D：語意層
├── proxy/                 ← B：agent 防護
├── extension/             ← C：瀏覽器擴充
├── data/                  ← D：語料（注意：真實資料不上傳）
└── docs/                  ← 全員：文件
```
