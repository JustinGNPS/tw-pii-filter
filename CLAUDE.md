# CLAUDE.md

## 測試

- 一律用 `python -m pytest tests/` 執行測試，**不要用 `python -m unittest discover`**。
  本專案的測試檔案混用了 `unittest.TestCase` 風格與純 pytest 風格（`test_proxy.py`、
  `test_mapping.py`、`test_masker.py`、`test_restorer.py`、`test_sse_restorer.py`），
  `unittest discover` 會**靜默跳過**純 pytest 風格的測試（不報錯、也不計入總數），
  容易誤以為測試都跑了、其實漏了一大批。
