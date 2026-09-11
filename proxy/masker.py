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

from core.risk import reduction
from core.risk.combination_risk import compute_combination_risk
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

    直接識別子換成佔位符後字串長度會改變，所以不能沿用原始 start/end；但
    SKIP_TYPES 的真值仍原樣存在，依原始出現順序搜尋即可取得安全的新座標。
    """
    located: list[dict] = []
    cursor = 0
    for span in sorted(spans, key=lambda item: item.get("start", 0)):
        pii_type = normalize_type(span.get("type", ""))
        if pii_type not in selected_types or pii_type in reduction.TEXT_GENERALIZATION_TYPES:
            continue
        value = span.get("text") or ""
        if not value:
            continue
        start = text.find(value, cursor)
        if start < 0:
            start = text.find(value)
        if start < 0:
            continue
        end = start + len(value)
        located.append({**span, "type": pii_type, "start": start, "end": end})
        cursor = end
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
