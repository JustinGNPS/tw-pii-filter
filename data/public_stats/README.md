# Layer 3 公開人口統計快照

`tw_population_age_sex_region_202607.json` 是 Layer 3 執行時使用的離線彙整資料，
來源為內政部統計處在政府資料開放平臺發布的
[人口數單一年齡組─按性別、區域別分](https://data.gov.tw/dataset/14226)。

- 資料月份：2026-07（民國 115 年 7 月）
- 維度：縣市、性別、單一年齡（0–99 歲；另保留 100 歲以上與年齡不詳總數）
- 內容：只有群組人數，沒有姓名、地址或任何個人層級紀錄
- 執行方式：程式只讀取此快照，不會在分析使用者文字時連外

## 更新方式

1. 從上述資料集的「人口數單一年齡組─按性別、區域別分」統計查詢下載單一月份的 UTF-8 CSV。
2. 執行：

   ```bash
   python tools/build_population_snapshot.py downloaded.csv data/public_stats/tw_population_age_sex_region_YYYYMM.json --period YYYY-MM
   ```

3. 更新 `core/risk/population_estimator.py` 的 `SNAPSHOT_PATH`，執行完整測試後再送 PR。

產生工具會驗證 22 個縣市、全國總計、三種性別列，以及男女總人口可對上性別總計，
避免官方匯出格式改變時靜默產生錯誤快照。
