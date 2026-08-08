"""遮蔽：把請求 payload 裡的真實個資換成佔位符。

流程：`detector.scan_payload()` 找出每個文字欄位裡的 span
→ `MappingTable` 配佔位符 → **從後往前**替換 → 寫回原欄位。

## 為什麼一定要從後往前

替換會改變文字長度。`A123456789`（10 字）換成 `[TW_ID_1]`（9 字）之後，
整段文字短了 1 個字，**後面所有 span 的 start/end 就全部偏掉了**。
由座標大的往小的替換，每次替換只影響已經處理完的部分。

A 的 `detect_all()` 內部已做 Layer 4 重疊仲裁，spans 保證互不重疊，
因此這裡不需要再處理重疊情形。

## 不是每一筆偵測到的東西都該遮

語意層會回傳職稱這類「認得出來、但不是個資」的實體。遮掉它們對隱私沒有
任何幫助，卻會讓 agent 讀不懂上下文。哪些型別跳過由 `config.SKIP_TYPES`
決定（預設 `POSITION`），理由寫在 `proxy/config.py`。
"""

from collections.abc import Iterable

from proxy import config, detector
from proxy.cache import DetectionCache
from proxy.mapping import MappingTable, normalize_type


def resolve_skip_types(skip_types: Iterable[str] | None) -> frozenset[str]:
    """決定這次要跳過哪些型別；`None` 表示採用設定檔的預設值。

    傳進來的代碼一律正規化，否則呼叫端寫小寫的 `position` 會靜默失效。
    """
    if skip_types is None:
        return config.SKIP_TYPES
    return frozenset(normalize_type(item) for item in skip_types)


def _to_mask(spans: list[dict], skip: frozenset[str]) -> list[dict]:
    """濾掉不該遮蔽的型別。"""
    return [span for span in spans if normalize_type(span["type"]) not in skip]


def mask_text(
    text: str,
    spans: list[dict],
    table: MappingTable,
    skip_types: Iterable[str] | None = None,
) -> str:
    """把單一段文字裡的所有 span 換成佔位符（跳過的型別原樣保留）。"""
    skip = resolve_skip_types(skip_types)
    kept = sorted(_to_mask(spans, skip), key=lambda s: s["start"])

    # 先依「出現順序」配號碼，再從後往前替換。
    # 兩件事要分開做：替換必須倒著來（否則座標偏掉），但發號碼不該跟著倒過來，
    # 不然文章裡第一個出現的人會拿到比較大的號碼。功能上無害，但看的人會困惑。
    tokens = [table.token_for(span["type"], span["text"]) for span in kept]

    for span, token in zip(reversed(kept), reversed(tokens)):
        text = text[: span["start"]] + token + text[span["end"] :]
    return text


def mask_payload(
    payload: dict,
    table: MappingTable,
    cache: DetectionCache | None = None,
    skip_types: Iterable[str] | None = None,
) -> dict[str, int]:
    """就地遮蔽整包 payload，回傳「型別 -> 遮蔽筆數」的摘要。

    摘要**不含任何原始個資內容**，可安全寫進 log。
    沒偵測到東西時回傳空 dict，payload 完全不會被動到。

    摘要統計的是**實際遮掉的筆數**，不是偵測到的筆數 —— 跳過的型別不列入，
    否則 log 會宣稱「已遮蔽 N 筆」而其中有些根本沒被動過。型別代碼一律是
    正規化後的形式，與實際發出去的佔位符一致。
    """
    skip = resolve_skip_types(skip_types)
    counts: dict[str, int] = {}

    for result in detector.scan_payload(payload, cache):
        spans = _to_mask(result["spans"], skip)
        if not spans:
            continue  # 這個欄位偵測到的全被跳過，原樣保留
        detector.set_at(
            payload, result["path"], mask_text(result["text"], spans, table, skip)
        )
        for span in spans:
            pii_type = normalize_type(span["type"])
            counts[pii_type] = counts.get(pii_type, 0) + 1

    return counts
