"""
Layer 3：組合風險評分
負責人：D

依據：
  - docs/layer3_spec.md（A 在 PR #18 定案的正式規格）
  - 專題報告第 6.2 節、第 12 節 R4：「先做簡化版（共現計數），再談 k-anonymity」
  - C 在 PR #17 review 的三個建議：補上 ADDRESS/SCENE、擴充年齡格式、差異化權重

核心概念（報告第 4.3 節）：
    llm-redactor 論文實測：就算把明顯的 PII 字串都遮掉，「隱含身分」（無 PII
    字串但仍能指認個人）洩漏率仍達 95%。例如沒寫姓名，但寫了「35歲、新竹、
    資深後端工程師」，組合起來還是能定位到特定人。這正是本模組要處理的問題。

目標是「偵測並警示，讓使用者自己判斷」，不是「完全自動解決」（layer3_spec.md）——
這是公認的開放問題，簡化版就有價值，不用做到完美。

輸出格式（對齊 layer3_spec.md）：
    {
        "score": 0.82,
        "contributing_types": ["AGE", "ADDRESS", "POSITION"],
        "risk_level": "高",
        "suggestions": ["「35歲」可泛化為「30-35歲」", ...]
    }

v1 設計取捨（layer3_spec.md 明確定義為兩版）：
    - 簡化版（本模組，第一優先）：共現計數 + 依識別力差異化權重，規則式計分。
    - 進階版（行有餘力）：用台灣人口統計資料做真正的 k-anonymity 估算，
      需要背景母體資料庫，目前沒有這種資料可用，留待 v2。
"""

import re
from datetime import date
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 準識別子權重表
#
# 依「這個類別平均能把母體縮小多少」給差異化權重，不是齊頭式平等（C 在
# PR #17 review 指出：COMPANY 跟 ORGANIZATION 給一樣的權重分不出「台積電」
# （三萬人）跟「某三人新創」的識別力差好幾個數量級——這屬於 v2 才處理的
# 精確度問題，v1 先用「平均而言這個類別的識別力」給一個粗略但有理由的數字，
# 不是隨手訂的齊頭權重）。
#
# 權重依據：
#   AGE（尤其精確到出生年次）: 識別力最高，年齡層級細，母體縮小比例最大
#   ADDRESS（到區級）: Sweeney (2000) 經典研究——郵遞區號+生日+性別即可
#     唯一識別 87% 美國人口，地區是最強的準識別子之一
#   POSITION: 中等，職稱範圍通常比地區/年齡窄，但仍有相當識別力
#   GENDER: 單獨識別力低（只能排除約一半母體），但常跟其他準識別子疊加
#   COMPANY / ORGANIZATION / GOVERNMENT: 中低，視規模差異極大（v1 先給
#     同一個粗略權重，v2 再依規模精算）
#   SCENE（常去的特定地點）: 較低，但仍有識別力（C 建議納入、給較低權重）
# ---------------------------------------------------------------------------
WEIGHT_BY_TYPE: Dict[str, float] = {
    "AGE": 0.35,
    "ADDRESS": 0.30,
    "POSITION": 0.20,
    "GENDER": 0.15,
    "COMPANY": 0.15,
    "ORGANIZATION": 0.15,
    "GOVERNMENT": 0.15,
    "SCENE": 0.10,
}

# 只有這些型別會被計入組合風險（對應 NER 型別裡「刻意不遮蔽、單獨看不是
# 個資」的類別，加上 AGE/GENDER 這兩個 NER 沒有原生標籤、需要獨立偵測的類別）。
QUASI_IDENTIFIER_TYPES = set(WEIGHT_BY_TYPE.keys())

RISK_SCORE_CAP = 1.0

# 「值得警告」的分數門檻，供 proxy（B）決定何時用 WARNING 等級印 log。
# 設在 0.6（約對應 3 種中高權重準識別子共現）不是隨便訂的：報告第 4.3 節
# 引用的 Sweeney (2000) 經典發現 —— 性別＋郵遞區號＋生日「三個」準識別子
# 就足以重新識別 87% 的美國人口。
WARNING_THRESHOLD = 0.6

# ---------------------------------------------------------------------------
# AGE：NER 模型沒有這個標籤，獨立用正則抓取，不經過 detect_ner()/detect_all()
# 的 span 格式。支援台灣常見的幾種年齡/出生年寫法（C review 指出的缺口）。
# ---------------------------------------------------------------------------

# 阿拉伯數字 + 歲（例如「35歲」），排除「歲數」這種非年齡用法
_AGE_DIGIT_PATTERN = re.compile(r"(?<![0-9])([1-9][0-9]?)\s*歲(?!數)")

# 民國 NN 年次 / 民國 NN 年生（台灣醫療對話、履歷語料常見寫法）
_AGE_MINGUO_PATTERN = re.compile(r"民國\s*([0-9]{1,3})\s*年(?:次|生)?")

# 西元 NNNN 年生（例如「1989年生」）
_AGE_WESTERN_YEAR_PATTERN = re.compile(r"((?:19|20)[0-9]{2})\s*年生")

# 中文數字年齡（例如「三十五歲」），支援 0～99 的常見組合寫法
_CN_DIGIT_MAP = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_AGE_CHINESE_PATTERN = re.compile(
    r"([一二兩三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九])歲"
)


def _chinese_number_to_int(cn: str) -> Optional[int]:
    """把「三十五」「二十」「九」這類中文數字（0～99）轉成整數，轉不了回 None。"""
    if not cn:
        return None
    if cn in _CN_DIGIT_MAP:
        return _CN_DIGIT_MAP[cn]
    if "十" in cn:
        left, _, right = cn.partition("十")
        tens = _CN_DIGIT_MAP.get(left, 1) if left else 1  # 「十五」的「十」前面沒數字，視為 1
        ones = _CN_DIGIT_MAP.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def _extract_age(text: str, today: Optional[date] = None) -> Optional[int]:
    """
    嘗試從文字裡抓出一個具體年齡數字（供泛化建議用）。抓不到就回傳 None
    （仍然可能判定「有 AGE 這個準識別子」，只是沒有精確數字可以泛化）。
    """
    today = today or date.today()

    m = _AGE_DIGIT_PATTERN.search(text)
    if m:
        return int(m.group(1))

    m = _AGE_MINGUO_PATTERN.search(text)
    if m:
        roc_year = int(m.group(1))
        return today.year - (roc_year + 1911)

    m = _AGE_WESTERN_YEAR_PATTERN.search(text)
    if m:
        birth_year = int(m.group(1))
        return today.year - birth_year

    m = _AGE_CHINESE_PATTERN.search(text)
    if m:
        age = _chinese_number_to_int(m.group(1))
        if age is not None:
            return age

    return None


def _has_age(text: str) -> bool:
    return (
        _AGE_DIGIT_PATTERN.search(text) is not None
        or _AGE_MINGUO_PATTERN.search(text) is not None
        or _AGE_WESTERN_YEAR_PATTERN.search(text) is not None
        or _AGE_CHINESE_PATTERN.search(text) is not None
    )


# ---------------------------------------------------------------------------
# GENDER：同樣不在 NER 標籤裡，用簡單關鍵字判斷（v1 啟發式，容易有 false
# positive，例如「小姐姐」這種非性別語境；先求有、之後再精修）。
# ---------------------------------------------------------------------------
_GENDER_KEYWORDS = ("男性", "女性", "先生", "小姐", "太太", "女士")


def _has_gender(text: str) -> bool:
    return any(kw in text for kw in _GENDER_KEYWORDS)


# ---------------------------------------------------------------------------
# 泛化建議：對應 layer3_spec.md「建議的泛化方式（例如「32歲」建議泛化成
# 「30-35歲」）」。v1 先給每個型別一句通用建議，AGE 有抓到精確數字時
# 給計算過的區間建議。
# ---------------------------------------------------------------------------

def _age_generalization_suggestion(text: str) -> Optional[str]:
    age = _extract_age(text)
    if age is None:
        return "文字中的年齡資訊建議泛化為 5 歲一個區間（例如「32歲」→「30-35歲」）"
    bucket_start = (age // 5) * 5
    bucket_end = bucket_start + 4
    return f"「{age}歲」建議泛化為「{bucket_start}-{bucket_end}歲」"


_GENERIC_SUGGESTIONS = {
    "ADDRESS": "地址建議泛化到市/縣級（例如「信義區光復路259巷」→「台北市」）",
    "POSITION": "職稱可保留，但建議避免同時透露服務公司名稱",
    "GENDER": "若非必要，建議省略性別資訊",
    "COMPANY": "公司名稱可模糊化為產業別（例如「某科技公司」）",
    "ORGANIZATION": "機構名稱可模糊化為機構類型",
    "GOVERNMENT": "政府機關名稱可模糊化為機關層級（例如「某地方政府機關」）",
    "SCENE": "常去地點建議降低描述精確度",
}


def _build_suggestions(text: str, contributing_types: List[str]) -> List[str]:
    suggestions = []
    for t in contributing_types:
        if t == "AGE":
            suggestions.append(_age_generalization_suggestion(text))
        else:
            suggestion = _GENERIC_SUGGESTIONS.get(t)
            if suggestion:
                suggestions.append(suggestion)
    return suggestions


def _risk_level(score: float) -> str:
    """對應 layer3_spec.md 的三級分類（高/中/低）。"""
    if score >= WARNING_THRESHOLD:
        return "高"
    if score >= 0.3:
        return "中"
    return "低"


def is_warning_worthy(risk: Dict) -> bool:
    """判斷一筆 combination_risk 結果是否達到值得警告的門檻（供 B 的 log 邏輯用）。"""
    return risk.get("score", 0.0) >= WARNING_THRESHOLD


def compute_combination_risk(text: str, spans: Optional[List[Dict]] = None) -> Dict:
    """
    計算一份文字的組合風險分數。

    Args:
        text: 原始文字。
        spans: 已知的偵測結果（例如 detect_all() 的輸出），用來抓取
            POSITION/COMPANY/ADDRESS 這類準識別子。若為 None，只會用
            內部的 AGE/GENDER 偵測掃描文字本身。

    重要：text 與 spans 必須是同一份「視角」下的內容，兩者要互相對應——
    這個函式評估的是「你給的這份 text，實際看得到多少準識別子」，不是
    「這段內容理論上曾經含有什麼」。因此內部才會直接對 text 本身做
    AGE/GENDER 正則掃描，而不是只信任 spans。

    兩種合法用法（B 在 proxy 端曾提出這個疑問，這裡明確記錄）：
        - text=原文, spans=原文的完整偵測結果
          → 回答「這段內容本身潛在風險多高」（適合分析語料、評估資料集敏感度）
        - text=遮蔽後的文字, spans=沒被遮掉、仍留在文字裡的準識別子（殘餘 spans）
          → 回答「送出去的內容，遮蔽完之後還剩多少風險」（適合載體端警告使用者，
            proxy／擴充都該用這個版本，避免對已經被遮蔽掉的資訊重複計分而虛報）

    一種錯誤用法：
        - text=遮蔽後文字, spans=原文的完整偵測結果（兩者視角不一致，
          會把已經遮掉的準識別子也算進分數，虛報風險）

    Returns:
        Dict: {
            "score": float,              # 0.0～1.0
            "contributing_types": [str], # 依字母排序、去重
            "risk_level": str,           # "高" / "中" / "低"
            "suggestions": [str],        # 每個貢獻型別對應的泛化建議
        }
    """
    contributing_types = set()

    if spans:
        for span in spans:
            span_type = span.get("type", "")
            if span_type in QUASI_IDENTIFIER_TYPES:
                contributing_types.add(span_type)

    if _has_age(text):
        contributing_types.add("AGE")
    if _has_gender(text):
        contributing_types.add("GENDER")

    n = len(contributing_types)
    if n >= 2:
        score = min(RISK_SCORE_CAP, sum(WEIGHT_BY_TYPE.get(t, 0.15) for t in contributing_types))
    else:
        score = 0.0

    contributing_sorted = sorted(contributing_types)

    return {
        "score": round(score, 3),
        "contributing_types": contributing_sorted,
        "risk_level": _risk_level(score),
        "suggestions": _build_suggestions(text, contributing_sorted) if score > 0 else [],
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
            "text": "民國78年次，男性，住在信義區，在某科技公司上班。",
            "spans": [
                {"start": 15, "end": 18, "type": "ADDRESS", "text": "信義區"},
                {"start": 20, "end": 25, "type": "COMPANY", "text": "某科技公司"},
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