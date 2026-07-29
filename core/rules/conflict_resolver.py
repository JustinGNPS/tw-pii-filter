"""Layer 4：spans 重疊衝突解析。

多個規則各自偵測同一段文字時，可能產生重疊的 span（例如手機號碼剛好是
email local-part 的一部分）。本模組負責在合併後的 span 清單中，針對重疊
片段依優先順序保留其中一個、移除其餘的，確保輸出的 spans 互不重疊，讓
下游可以安全地依座標（start/end）直接替換文字。

優先順序：
1. 範圍（end - start）較大者優先
2. 範圍相同時，confidence 較高者優先
3. 以上皆相同時，source == "rule" 優先於 source == "model"
"""


def _priority_key(span: dict) -> tuple:
    length = span["end"] - span["start"]
    source_rank = 0 if span.get("source") == "rule" else 1
    return (-length, -span["confidence"], source_rank)


def _overlaps(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def resolve_overlaps(spans: list) -> list:
    """依優先順序解析重疊的 span，回傳互不重疊的 span 清單（依 start 升序排列）。

    採貪婪演算法：先依優先順序排序所有候選 span，再依序嘗試保留，
    只要與目前已保留的任何 span 重疊就捨棄，藉此確保結果彼此互不重疊。
    """
    accepted = []
    for span in sorted(spans, key=_priority_key):
        if not any(_overlaps(span, kept) for kept in accepted):
            accepted.append(span)

    accepted.sort(key=lambda span: (span["start"], span["end"]))
    return accepted
