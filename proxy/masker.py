"""遮蔽：把請求 payload 裡的真實個資換成佔位符。

流程：`detector.scan_payload()` 找出每個文字欄位裡的 span
→ `MappingTable` 配佔位符 → **從後往前**替換 → 寫回原欄位。

## 為什麼一定要從後往前

替換會改變文字長度。`A123456789`（10 字）換成 `[TW_ID_1]`（9 字）之後，
整段文字短了 1 個字，**後面所有 span 的 start/end 就全部偏掉了**。
由座標大的往小的替換，每次替換只影響已經處理完的部分。

A 的 `detect_all()` 內部已做 Layer 4 重疊仲裁，spans 保證互不重疊，
因此這裡不需要再處理重疊情形。
"""

from proxy import detector
from proxy.cache import DetectionCache
from proxy.mapping import MappingTable


def mask_text(text: str, spans: list[dict], table: MappingTable) -> str:
    """把單一段文字裡的所有 span 換成佔位符。"""
    for span in sorted(spans, key=lambda s: s["start"], reverse=True):
        token = table.token_for(span["type"], span["text"])
        text = text[: span["start"]] + token + text[span["end"] :]
    return text


def mask_payload(
    payload: dict, table: MappingTable, cache: DetectionCache | None = None
) -> dict[str, int]:
    """就地遮蔽整包 payload，回傳「型別 -> 遮蔽筆數」的摘要。

    摘要**不含任何原始個資內容**，可安全寫進 log。
    沒偵測到東西時回傳空 dict，payload 完全不會被動到。
    """
    results = detector.scan_payload(payload, cache)
    for result in results:
        masked = mask_text(result["text"], result["spans"], table)
        detector.set_at(payload, result["path"], masked)
    return detector.summarize(results)
