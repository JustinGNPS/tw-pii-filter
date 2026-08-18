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

from proxy import config, detector, risk
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

    只要遮蔽結果、不需要組合風險評分時用這個；兩者都要就用
    `mask_payload_with_risk()`。
    """
    counts, _ = mask_payload_with_risk(payload, table, cache, skip_types)
    return counts


def mask_payload_with_risk(
    payload: dict,
    table: MappingTable,
    cache: DetectionCache | None = None,
    skip_types: Iterable[str] | None = None,
) -> tuple[dict[str, int], dict | None]:
    """就地遮蔽整包 payload，回傳 (遮蔽筆數摘要, 組合風險評分)。

    摘要是「型別 -> 遮蔽筆數」，**不含任何原始個資內容**，可安全寫進 log。
    沒偵測到東西時回傳空 dict，payload 完全不會被動到。

    摘要統計的是**實際遮掉的筆數**，不是偵測到的筆數 —— 跳過的型別不列入，
    否則 log 會宣稱「已遮蔽 N 筆」而其中有些根本沒被動過。型別代碼一律是
    正規化後的形式，與實際發出去的佔位符一致。

    第二個回傳值是這包 payload 裡**分數最高的那一個欄位**的組合風險評分
    （見 `proxy/risk.py`），沒有任何文字欄位時為 `None`。風險評分只是資訊，
    **不影響遮蔽結果** —— 這個函式對 payload 做的事，跟沒有 Layer 3 時
    完全一樣。
    """
    skip = resolve_skip_types(skip_types)
    counts: dict[str, int] = {}
    residual_by_path: dict[detector.Path, list[dict]] = {}

    for result in detector.scan_payload(payload, cache):
        # 先記下「偵測到、但不會被遮掉」的 spans。這是組合風險真正要看的東西：
        # 被遮掉的型別對重新識別已經沒有貢獻了（理由見 proxy/risk.py）。
        residual = risk.residual_spans(result["spans"], skip)
        if residual:
            residual_by_path[result["path"]] = residual

        spans = _to_mask(result["spans"], skip)
        if not spans:
            continue  # 這個欄位偵測到的全被跳過，原樣保留
        detector.set_at(
            payload, result["path"], mask_text(result["text"], spans, table, skip)
        )
        for span in spans:
            pii_type = normalize_type(span["type"])
            counts[pii_type] = counts.get(pii_type, 0) + 1

    return counts, _assess_risk(payload, residual_by_path)


def _assess_risk(
    payload: dict, residual_by_path: dict[detector.Path, list[dict]]
) -> dict | None:
    """對已遮蔽的 payload 逐欄位評組合風險，回傳分數最高的那一筆。

    刻意重新走一次 `extract_texts()`，而不是沿用上面迴圈裡的欄位 ——
    `scan_payload()` 只回傳**有偵測到 span 的**欄位，但組合風險的
    `AGE`/`GENDER` 是 D 的模組自己用正則從文字裡抓的，不經過 span 機制，
    一個「35 歲女性工程師」的欄位在語意層關閉時一個 span 都不會有，卻仍
    有風險。只看有 span 的欄位會漏掉這種情況。

    這一趟只是走訪 payload 加跑幾個正則，沒有偵測成本。
    """
    if not config.ENABLE_RISK_WARNING:
        return None

    worst: dict | None = None
    for path, text in detector.extract_texts(payload):
        worst = risk.worse_of(worst, risk.assess(text, residual_by_path.get(path, [])))
    return worst
