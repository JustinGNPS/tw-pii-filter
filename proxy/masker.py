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

from core.risk import reduction
from core.risk.combination_risk import compute_combination_risk
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


def mask_with_residual(text, spans, table, skip_types=None):
    """遮蔽並精確平移保留 spans；不以搜尋同值字串猜測位置。"""
    skip = resolve_skip_types(skip_types)
    masked = mask_text(text, spans, table, skip)
    offset = 0
    residual = []
    for span in sorted(spans, key=lambda item: item["start"]):
        pii_type = normalize_type(span["type"])
        if pii_type in skip:
            residual.append({**span, "type": pii_type,
                             "start": span["start"] + offset, "end": span["end"] + offset})
        else:
            token = table.token_for(pii_type, span["text"])
            offset += len(token) - (span["end"] - span["start"])
    return masked, residual


def mask_payload(
    payload: dict,
    table: MappingTable,
    cache: DetectionCache | None = None,
    skip_types: Iterable[str] | None = None,
    risk_reduction_policy: str | None = None,
) -> dict[str, int]:
    """就地遮蔽整包 payload，回傳「型別 -> 遮蔽筆數」的摘要。

    只要遮蔽結果、不需要組合風險評分時用這個；兩者都要就用
    `mask_payload_with_risk()`。
    """
    counts, _ = mask_payload_with_risk(
        payload, table, cache, skip_types, risk_reduction_policy
    )
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
    risk_reduction_policy: str | None = None,
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
    reduction_policy = reduction.normalize_policy(
        config.RISK_REDUCTION_POLICY
        if risk_reduction_policy is None
        else risk_reduction_policy
    )
    counts: dict[str, int] = {}
    residual_by_path: dict[detector.Path, list[dict]] = {}

    for result in detector.scan_payload(payload, cache):
        # 先記下「偵測到、但不會被遮掉」的 spans。這是組合風險真正要看的東西：
        # 被遮掉的型別對重新識別已經沒有貢獻了（理由見 proxy/risk.py）。
        masked, residual = mask_with_residual(result["text"], result["spans"], table, skip)
        if residual:
            residual_by_path[result["path"]] = residual

        spans = spans_to_mask(result["spans"], skip)
        if not spans:
            continue  # 這個欄位偵測到的全被跳過，原樣保留
        detector.set_at(
            payload, result["path"], masked
        )
        for span in spans:
            pii_type = normalize_type(span["type"])
            counts[pii_type] = counts.get(pii_type, 0) + 1

    if reduction_policy != reduction.POLICY_OFF:
        # 第二趟只走訪已經遮蔽完成的文字，不重新跑 NER。除了較省成本，也確保
        # 規劃依據就是「雲端原本會看到的版本」，而不是含明碼 PII 的原文。
        for path, text in list(detector.extract_texts(payload)):
            reduced, final_spans, _, _, extra_counts = reduce_residual_text(
                text,
                residual_by_path.get(path, []),
                table,
                reduction_policy,
            )
            if reduced != text:
                detector.set_at(payload, path, reduced)
            if final_spans:
                residual_by_path[path] = final_spans
            else:
                residual_by_path.pop(path, None)
            for pii_type, count in extra_counts.items():
                counts[pii_type] = counts.get(pii_type, 0) + count

    return counts, _assess_risk(payload, residual_by_path)


def _locate_surviving_spans(
    text: str, spans: list[dict], selected_types: set[str]
) -> list[dict]:
    """在已遮蔽文字裡重新定位仍保留的 NER span。

    直接識別子換成佔位符後字串長度會改變，呼叫端必須先用 mask_with_residual
    取得新座標。此處驗證原文一致，不搜尋同值文字，以免遮錯另一個出現位置。
    """
    located: list[dict] = []
    for span in sorted(spans, key=lambda item: item.get("start", 0)):
        pii_type = normalize_type(span.get("type", ""))
        if pii_type not in selected_types or pii_type in reduction.TEXT_GENERALIZATION_TYPES:
            continue
        value = span.get("text") or ""
        if not value:
            continue
        start, end = span["start"], span["end"]
        if text[start:end] != value:
            raise ValueError("殘餘 span 必須使用遮蔽後文字的座標")
        located.append({**span, "type": pii_type, "start": start, "end": end})
    return located


def reduce_residual_text(
    text: str,
    residual_spans: list[dict],
    table: MappingTable,
    policy: str,
) -> tuple[str, list[dict], dict, dict, dict[str, int]]:
    """對一段遮蔽後文字套用 opt-in 降階，供正式 proxy 與 Demo 共用。"""
    normalized = reduction.normalize_policy(policy)
    initial = compute_combination_risk(text, residual_spans)
    plan = reduction.plan_reduction(initial, normalized)
    selected = set(plan["applied_types"])

    located = _locate_surviving_spans(text, residual_spans, selected)
    extra_counts: dict[str, int] = {}
    if located:
        text = mask_text(text, located, table, skip_types=())
        for span in located:
            pii_type = normalize_type(span["type"])
            extra_counts[pii_type] = extra_counts.get(pii_type, 0) + 1

    text, generalized = reduction.generalize_text_types(text, selected)
    generalized_by_type = {item["type"]: item for item in generalized}
    masked_counts = {item: count for item, count in extra_counts.items()}

    final_spans = [
        span
        for span in residual_spans
        if normalize_type(span.get("type", "")) not in selected
    ]
    final = compute_combination_risk(text, final_spans)

    enriched_steps = []
    for step in plan["steps"]:
        item = dict(step)
        if step["method"] == "generalize":
            action = generalized_by_type.get(step["type"], {})
            item["occurrences"] = action.get("occurrences", 0)
            item["replacement"] = action.get("replacement", "")
        else:
            item["occurrences"] = masked_counts.get(step["type"], 0)
            item["replacement"] = "可還原佔位符"
        enriched_steps.append(item)

    plan = {**plan, "steps": enriched_steps, "final_score": final["score"]}
    if normalized == reduction.POLICY_STRICT:
        plan["met_target"] = not final["contributing_types"]
    elif normalized != reduction.POLICY_OFF:
        plan["met_target"] = final["score"] < float(plan["threshold"])

    return text, final_spans, final, plan, extra_counts


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
