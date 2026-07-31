"""產生 Python 規則層對 parity 語料的偵測結果，作為 TypeScript 版的比對基準。

用途：擴充（載體一）在瀏覽器裡跑不了 Python，規則層必須另外有 TypeScript 版。
兩版一旦分歧，同一段文字在瀏覽器擴充與 proxy 就會有不同的遮蔽結果，
這是最難察覺、也最傷公信力的 bug。本腳本把 Python 版的輸出固定成快照，
由 `extension/tests/parity.test.ts` 逐筆比對，任何分歧都會讓測試失敗。

用法（在 repo 根目錄執行）：
    python tools/gen_parity_expected.py

Python 版邏輯有任何變更時，都要重跑本腳本並把更新後的
`tests/fixtures/parity_expected.json` 一起 commit。
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.rules import detect_all  # noqa: E402  （需先設定 sys.path）

FIXTURES = REPO_ROOT / "tests" / "fixtures"

CASES_PATH = FIXTURES / "parity_cases.json"
EXPECTED_PATH = FIXTURES / "parity_expected.json"

# 已知 Python / JavaScript 行為不同的語料（見 docs/ts_port.md）。
# 這批不要求兩版一致，但要求「分歧維持在已記錄的樣子」，
# 一旦有人修好或改壞，extension/tests/known_divergence.test.ts 就會失敗。
DIVERGENCE_CASES_PATH = FIXTURES / "divergence_cases.json"
DIVERGENCE_PYTHON_PATH = FIXTURES / "divergence_python.json"


def _dump(path: Path, payload: object) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> None:
    with open(CASES_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    expected = [detect_all(case) for case in cases]
    _dump(EXPECTED_PATH, expected)

    total_spans = sum(len(result["spans"]) for result in expected)
    print(f"已寫入 {EXPECTED_PATH.relative_to(REPO_ROOT)}")
    print(f"語料 {len(cases)} 筆，共偵測到 {total_spans} 個 span")

    with open(DIVERGENCE_CASES_PATH, encoding="utf-8") as f:
        divergence_cases = json.load(f)

    _dump(DIVERGENCE_PYTHON_PATH, [detect_all(case) for case in divergence_cases])
    print(f"已寫入 {DIVERGENCE_PYTHON_PATH.relative_to(REPO_ROOT)}（{len(divergence_cases)} 筆已知分歧語料）")


if __name__ == "__main__":
    main()
