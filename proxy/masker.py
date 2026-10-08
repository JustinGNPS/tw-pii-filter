"""遮蔽 payload：把請求裡的真實個資換成佔位符。

純字串層的遮蔽邏輯住在 `core/redact/masker.py`（兩個載體共用，issue #23），
這裡放的是 **proxy 專屬**的那一半：

流程：`detector.scan_payload()` 從 OpenAI／Anthropic 的 payload 裡找出每個
文字欄位與其中的 span → 交給 `core.redact.mask_text()` 替換 → 寫回原欄位
→ 順便算組合風險。

## 為什麼這一半不搬進 core

它認得 API 協定（`detector`）、讀 proxy 的環境變數（`config`）、印 proxy 的
風險警示（`risk`）。搬進中性套件會讓 `core/` 反過來相依 `proxy/`，本末倒置。
C 的擴充面對的是使用者貼上的一段文字，本來就用不到 payload 走訪。

## 不是每一筆偵測到的東西都該遮

語意層會回傳職稱這類「認得出來、但不是個資」的實體。遮掉它們對隱私沒有
任何幫助，卻會讓 agent 讀不懂上下文。哪些型別跳過由 `config.SKIP_TYPES`
決定（預設 `POSITION`／`COMPANY`），理由寫在 `proxy/config.py`。

**這個預設是政策，所以由這裡注入**：`core.redact.mask_text()` 自己不帶任何
預設值（`skip_types=None` 在那邊是「一種都不跳過」），proxy 的預設在
`resolve_skip_types()` 解出來之後才傳進去。
"""

from collections.abc import Iterable

from core.redact.mapping import MappingTable, normalize_type
from core.redact.masker import mask_text as _mask_text
from core.redact.masker import spans_to_mask
from proxy import config, detector, risk
from proxy.cache import DetectionCache


def resolve_skip_types(skip_types: Iterable[str] | None) -> frozenset[str]:
    """決定這次要跳過哪些型別；`None` 表示採用設定檔的預設值。

    傳進來的代碼一律正規化，否則呼叫端寫小寫的 `position` 會靜默失效。
    """
    if skip_types is None:
        return config.SKIP_TYPES
    return frozenset(normalize_type(item) for item in skip_types)


def mask_text(
    text: str,
    spans: list[dict],
    table: MappingTable,
    skip_types: Iterable[str] | None = None,
) -> str:
    """把單一段文字裡的所有 span 換成佔位符（跳過的型別原樣保留）。

    與 `core.redact.mask_text()` 的差別只有一個：`skip_types=None` 在這裡
    代表「用 proxy 的預設（`config.SKIP_TYPES`）」，在 core 那邊代表
    「一種都不跳過」。替換邏輯（從後往前、發號順序）完全共用同一份。
    """
    return _mask_text(text, spans, table, resolve_skip_types(skip_types))


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


def new_value_counts(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """比對兩次 `MappingTable.issued_counts()`，算出「這一輪新增的不重複真值」。

    為什麼需要這個：agent 每輪都重送整段對話歷史，同一批個資每輪都會被重新
    掃到、重新遮蔽。若直接印該輪遮掉的**筆數**，數字會隨對話變長一路往上爬
    （實測一次 Codex 工作階段：6 -> 8 -> 14 -> 17，其中後面三輪根本沒有任何
    新個資），使用者無從分辨「又有新個資送出去了」與「還是原來那批」。

    清空後（閒置逾時）計數會歸零，`after < before`。這種情況代表整張表重新
    發過號，該型別現有的號碼**全部**是這一輪新配的，因此取 `after`。
    """
    new: dict[str, int] = {}
    for pii_type, count in after.items():
        previous = before.get(pii_type, 0)
        delta = count - previous if count >= previous else count
        if delta > 0:
            new[pii_type] = delta
    return new


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

        spans = spans_to_mask(result["spans"], skip)
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
