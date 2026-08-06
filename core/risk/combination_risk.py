"""
Layer 3：組合風險評分（簡化版）
負責人：D

依據專題報告第 6.2 節、第 12 節 R4：
    「組合風險評分難度超預期 → 先做簡化版（共現計數），再談 k-anonymity」

核心概念（報告第 4.3 節）：
    llm-redactor 論文實測：就算把明顯的 PII 字串都遮掉，「隱含身分」（無 PII
    字串但仍能指認個人）洩漏率仍達 95%。例如沒寫姓名，但寫了「35歲、新竹、
    資深後端工程師」，組合起來還是能定位到特定人。這正是本模組要處理的問題。

輸出格式對應報告第 6.1 節：
    {
        "score": 0.82,
        "contributing_types": ["AGE", "POSITION", "COMPANY"]
    }

設計取捨（v1，簡化版）：
    - 不做完整的 k-anonymity 估算（需要背景母體資料庫才能算「這個組合在
      人口中有多獨特」，目前沒有這種資料可用）。
    - 改用「準識別子種類數」做共現計數：出現的準識別子類別越多，風險分數
      越高。這呼應 k-anonymity 文獻裡的經典發現（Sweeney 2000：性別+郵遞區號
      +生日就能重新識別美國 87% 人口）——不需要精算，類別數本身就是強訊號。
    - AGE 目前不在 NER 的 14 種型別裡（模型是中國語料訓練的通用 NER，沒有
      年齡這個標籤），用獨立的輕量正則抓取，不經過 detect_ner()/detect_all()
      的 span 格式，避免這裡的實驗性設計影響到 interface.md 的既定契約。
    - QUASI_IDENTIFIER_TYPES 目前只納入 POSITION、COMPANY、ORGANIZATION、
      GOVERNMENT 這幾種「刻意不遮蔽、單獨看不是個資」的型別。NAME、ADDRESS
      等會被直接遮蔽的型別不計入（它們屬於 L1/L2 的直接識別子，risk 已經
      在遮蔽層處理，不是 Layer 3 要處理的「隱含身分」風險）。
"""

import re
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 準識別子型別清單：對應 NER 輸出裡「刻意不遮蔽」的型別。
# 若之後團隊決定其他型別（例如 email/mobile）的遮蔽政策改變，這份清單要同步調整。
# ---------------------------------------------------------------------------
QUASI_IDENTIFIER_TYPES = {"POSITION", "COMPANY", "ORGANIZATION", "GOVERNMENT"}

# 年齡不在 NER 的 14 種型別裡，用輕量正則抓取（不經過 detect_ner()/detect_all()）
AGE_PATTERN = re.compile(r"(?<![0-9])([1-9][0-9]?)\s*歲(?!數)")

# 共現計數的權重：每多一種準識別子類別，風險往上加一段。
# 選擇線性遞增、封頂 1.0，是刻意保持公式透明、容易解釋給評審聽，
# 不是宣稱這是精確的統計模型 —— 這正是報告 R4 講的「簡化版」精神。
RISK_WEIGHT_PER_TYPE = 0.3
RISK_SCORE_CAP = 1.0


def _extract_age_types(text: str) -> bool:
    """輕量偵測文字裡有沒有「NN歲」這種年齡格式。"""
    return bool(AGE_PATTERN.search(text))


def compute_combination_risk(text: str, spans: Optional[List[Dict]] = None) -> Dict:
    """
    計算一份文字的組合風險分數（簡化版：共現計數）。

    Args:
        text: 原始文字。
        spans: 已知的偵測結果（例如 detect_all() 的輸出），用來抓取
            POSITION/COMPANY 這類準識別子。若為 None，只會用內部的
            年齡正則掃描文字本身。

    Returns:
        Dict: {"score": float, "contributing_types": List[str]}
            score 範圍 0.0～1.0，contributing_types 依字母排序、去重。
    """
    contributing_types = set()

    if spans:
        for span in spans:
            span_type = span.get("type", "")
            if span_type in QUASI_IDENTIFIER_TYPES:
                contributing_types.add(span_type)

    if _extract_age_types(text):
        contributing_types.add("AGE")

    n = len(contributing_types)
    # 單一類別（或完全沒有）不構成「組合」風險，分數為 0；
    # 兩種以上類別開始線性累加，封頂 1.0。
    score = min(RISK_SCORE_CAP, max(0, n - 1) * RISK_WEIGHT_PER_TYPE) if n >= 2 else 0.0

    return {
        "score": round(score, 3),
        "contributing_types": sorted(contributing_types),
    }


if __name__ == "__main__":
    # 本地測試 - 僅使用虛構情境，嚴禁真實個資
    test_cases = [
        {
            "text": "他今年35歲，是新竹某家公司的資深後端工程師。",
            "spans": [
                {"start": 10, "end": 12, "type": "COMPANY", "text": "某家公司"},
                {"start": 12, "end": 20, "type": "POSITION", "text": "資深後端工程師"},
            ],
        },
        {
            "text": "王小明在測試公司上班。",
            "spans": [
                {"start": 4, "end": 7, "type": "COMPANY", "text": "測試公司"},
            ],
        },
        {
            "text": "這是一段完全不含個資或準識別子的普通文字。",
            "spans": [],
        },
    ]

    for case in test_cases:
        result = compute_combination_risk(case["text"], case["spans"])
        print(f"文字：{case['text']}")
        print(f"  -> {result}\n")