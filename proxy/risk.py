"""組合風險提示（Layer 3）：把 D 的評分模組接上 proxy 的遮蔽流程。

## 這一層在解決什麼

規則層與語意層擋的是「字串本身就是識別碼」的東西 —— 身分證、手機號碼、
地址。但有一類洩漏遮蔽擋不住：沒有任何一個欄位算得上個資，組合起來卻指
得到人。「35 歲、女性、新竹市東區、資深後端工程師」就是典型例子（報告
第 4.3 節引用 llm-redactor 的實測：把明碼 PII 全部遮掉之後，「隱含身分」
洩漏率仍達 95%）。

評分演算法是 D 寫的（`core/risk/combination_risk.py`，規格見
`docs/layer3_spec.md`）。**本模組只負責接線**：決定拿什麼餵給它、什麼時候
講、怎麼講。警告門檻直接用 D 的 `is_warning_worthy()`，不自己另外訂一套 ——
門檻值有它的依據（Sweeney 2000：性別＋郵遞區號＋生日三個準識別子即可重新
識別 87% 美國人口），在 proxy 這一側複製一份只會讓兩邊哪天悄悄分歧。

## 只提示，不遮蔽

Layer 3 不會動 payload 一個字。算完分數就只是印一行 log，內容照常送出去。

這是 `docs/layer3_spec.md` 訂下的定位（「偵測並警示，讓使用者自己判斷」），
不是實作偷懶：要遮到什麼程度取決於使用者在做什麼任務，把年齡、性別、職稱
全遮掉之後 agent 會看不懂上下文而失去作用，而這個取捨只有使用者本人判斷
得了。

## 為什麼餵「遮蔽後」的文字，而不是原文

直覺上會想用原文算（資訊最完整），但那會**虛報**。`ADDRESS` 預設會被遮，
遮完之後 AI 看到的是 `[ADDRESS_1]`，地址對重新識別已經沒有貢獻了，卻仍會
被計入分數。反過來說 `POSITION` 預設不遮（`config.SKIP_TYPES`）、
`AGE`/`GENDER` 根本沒有任何一層在偵測 —— 這幾類才是真正會送出去的殘餘風險。

所以這裡餵的是**遮蔽後的文字**加上**沒被遮掉的 spans**，也就是「AI 實際
看得到的東西」。組合風險本來就該定義成「遮完之後還剩下多少」，用原文算等
於在報告一個已經被自己擋掉的風險。

（佔位符本身不會干擾評分：`[ADDRESS_1]` 這種字串是英數字加底線，D 的
`AGE`/`GENDER` 偵測都是中文正則，掃不到它。）

## 一個請求只印一行

agent 每一輪都會重送整段對話歷史，同一則高風險訊息會在每一輪重新出現。
若逐欄位印，log 會被同一段內容洗版。因此這裡逐欄位評分、**只留分數最高的
那一筆**，一個請求最多一行警示。
"""

from __future__ import annotations

from core.risk.combination_risk import (
    compute_combination_risk,
    is_warning_worthy,
)
from proxy import config
from proxy.mapping import normalize_type

__all__ = [
    "residual_spans",
    "assess",
    "worse_of",
    "is_warning_worthy",
    "format_warning",
]


def residual_spans(spans: list[dict], skip: frozenset[str]) -> list[dict]:
    """挑出「偵測到、但沒被遮掉」的 spans —— 也就是 AI 仍看得到原文的那些。

    `skip` 就是 `masker.resolve_skip_types()` 的結果，兩邊用同一份判斷依據，
    避免這裡自己另外定義「什麼會被遮」而跟實際遮蔽行為對不上。

    型別一律正規化後再回傳：語意層可能吐小寫代碼，而 D 的
    `QUASI_IDENTIFIER_TYPES` 比對的是大寫，不正規化會靜默地一筆都對不上。
    """
    kept = []
    for span in spans:
        pii_type = normalize_type(span["type"])
        if pii_type in skip:
            kept.append({**span, "type": pii_type})
    return kept


def assess(masked_text: str, spans: list[dict]) -> dict:
    """算一段（遮蔽後的）文字的組合風險，回傳 D 的評分結果。

    停用時回傳分數 0 的空結果，讓呼叫端不必到處寫 `if config...`。
    """
    if not config.ENABLE_RISK_WARNING:
        return {"score": 0.0, "contributing_types": [], "risk_level": "低",
                "suggestions": []}
    return compute_combination_risk(masked_text, spans)


def worse_of(current: dict | None, candidate: dict) -> dict:
    """兩筆評分取分數高的那筆（同分保留先來的，讓結果不受欄位順序影響）。"""
    if current is None or candidate["score"] > current["score"]:
        return candidate
    return current


def format_warning(risk: dict) -> str:
    """把評分結果格式化成多行警示訊息。

    **這裡會印出從使用者內容抓到的年齡數字**（例如「「35歲」建議泛化為
    「35-39歲」」）—— 這是刻意的，經使用者確認：log 只寫在本機、不外傳，
    而泛化建議不講具體數字就失去了可操作性。年齡本身也不在任何遮蔽型別裡，
    等於本來就會原樣送給雲端 AI，印在本機 log 不構成額外暴露。

    但除了建議句以外，這裡**不印任何原文片段** —— 只有型別代碼與分數。
    """
    types = "、".join(risk["contributing_types"])
    lines = [
        f"組合風險 {risk['score']:.2f}（{risk['risk_level']}）："
        f"{types} 同時出現，即使明碼個資已遮蔽，仍可能指認到特定個人"
    ]
    lines.extend(f"    · {s}" for s in risk["suggestions"])
    return "\n".join(lines)
