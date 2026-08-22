"""本機示範介面：把 proxy 平常只印在 log 裡的東西變成看得見的畫面。

## 這是什麼、不是什麼

**是**：一個給團隊內部操作、展示、找問題用的本機頁面。貼一段文字進去，
立刻看到偵測到什麼、遮蔽後長什麼樣、組合風險多少、花了幾毫秒，
以及 proxy 目前的設定與流量狀態。

**不是**：第三個載體。它不參與轉發、不改變任何 agent 看到的行為。
`/demo` 底下的端點與轉發路徑完全分離。

## 為什麼預設關閉

這個頁面會回傳**未遮蔽的原文與對照表**（那是明文個資），而且提供一個
不需要任何認證就能呼叫偵測核心的端點。平常跑 proxy 沒有理由開著它，
因此需要明確設定 `PII_ENABLE_DEMO=1`，否則所有端點一律回 404
（**回 404 而不是 403** —— 沒開就等於不存在，不要對外洩漏「這裡有東西」）。

搭配 uvicorn 預設只綁 `127.0.0.1`，這個頁面不會出現在區域網路上。

## 為什麼用自己的對照表與快取

示範用的對照表跟 agent 正在用的那張**分開**。共用的話，你在頁面上試打
幾段文字就會污染 agent 的佔位符號碼（`[TW_ID_1]` 變成 `[TW_ID_7]`），
更糟的是把示範資料的真值混進正在服務 agent 的對照表裡。

現況總覽（`/demo/status`）看的則是**真正的那張表與真實流量**，因為那一區
要回答的問題是「proxy 現在的狀態」，不是「我剛剛試打了什麼」。
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from proxy import config, detector, masker, risk
from proxy.cache import DetectionCache
from proxy.mapping import MappingTable, normalize_type

router = APIRouter(prefix="/demo", tags=["demo"])

_PAGE = Path(__file__).with_name("demo_page.html")

# 示範專用的對照表與快取，與 agent 正在用的那組完全分開（理由見模組說明）。
# 對照表不設閒置逾時：示範時常常打完一段停下來講解，中途被清空反而奇怪。
DEMO_TABLE = MappingTable(idle_timeout=None)
DEMO_CACHE = DetectionCache()


def _require_enabled() -> None:
    """沒開啟就當作這些路徑不存在。"""
    if not config.ENABLE_DEMO:
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def page() -> HTMLResponse:
    """示範頁面本體。HTML 獨立成一個檔案，不塞在 Python 字串裡。"""
    _require_enabled()
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))


@router.post("/scan")
async def scan(payload: dict) -> dict:
    """對一段文字跑完整流程：偵測 -> 遮蔽 -> 組合風險評分。

    **不會送去上游**，純本機運算，可以無限次操作。
    """
    _require_enabled()
    text = (payload or {}).get("text") or ""
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text 必須是字串")

    skip = masker.resolve_skip_types(None)

    started = time.perf_counter()
    spans = detector.detect(text, cache=DEMO_CACHE)["spans"]
    detect_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    masked = masker.mask_text(text, spans, DEMO_TABLE, skip)
    mask_ms = (time.perf_counter() - started) * 1000

    # 佔位符要用**實際發出去的那個**，不是偵測核心建議的 replacement ——
    # 號碼由 proxy 自己發（見 docs/B_design.md 決定 3），兩者可能不同。
    # mask_text 已經配過號，這裡再問一次拿到的是同一個，不會多發。
    detail = []
    for span in spans:
        pii_type = normalize_type(span["type"])
        skipped = pii_type in skip
        detail.append(
            {
                "start": span["start"],
                "end": span["end"],
                "type": pii_type,
                "text": span["text"],
                "source": span.get("source", ""),
                "confidence": span.get("confidence"),
                "skipped": skipped,
                "token": (
                    None if skipped else DEMO_TABLE.token_for(span["type"], span["text"])
                ),
            }
        )

    counts: dict[str, int] = {}
    for item in detail:
        if not item["skipped"]:
            counts[item["type"]] = counts.get(item["type"], 0) + 1

    # 對照表以 token 去重：同一個真值出現多次只列一列
    mapping: dict[str, str] = {}
    for item in detail:
        if item["token"]:
            mapping[item["token"]] = item["text"]

    residual = risk.residual_spans(spans, skip)
    risk_result = risk.assess(masked, residual) if text.strip() else None

    return {
        "masked": masked,
        "spans": detail,
        "counts": counts,
        "mapping": [{"token": k, "value": v} for k, v in mapping.items()],
        "risk": risk_result,
        "timing": {"detect_ms": round(detect_ms, 2), "mask_ms": round(mask_ms, 2)},
        "cache": DEMO_CACHE.stats(),
        "ner_enabled": config.ENABLE_NER,
        "skip_types": sorted(skip),
    }


@router.post("/restore")
async def restore(payload: dict) -> dict:
    """把一段含佔位符的文字換回真值 —— 示範「回程」那一半。

    真實運作時這是 agent 收到雲端回覆時發生的事。這裡讓使用者自己打一段
    「假的 AI 回覆」，就能在完全不呼叫上游的情況下把整個循環走完：

        你打的 -> 遮蔽後（雲端看到的）-> 模擬 AI 回覆 -> 還原後（你看到的）

    `unknown` 是**查不到對應真值**的佔位符數量。雲端 AI 可能自己編出沒發過的
    佔位符（幻覺），那種一律**原樣保留、絕不猜測**（見 docs/B_design.md 決定 5）
    —— 猜測等同於憑空捏造一筆個資塞進使用者的內容裡。
    """
    _require_enabled()
    text = (payload or {}).get("text") or ""
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text 必須是字串")

    started = time.perf_counter()
    restored_text, restored, unknown = DEMO_TABLE.restore_text(text)
    elapsed = (time.perf_counter() - started) * 1000

    return {
        "restored_text": restored_text,
        "restored": restored,
        "unknown": unknown,
        "timing": {"restore_ms": round(elapsed, 3)},
    }


@router.post("/reset")
async def reset() -> dict:
    """清空示範用的對照表，讓佔位符號碼從 1 重新開始。

    只影響示範，agent 正在用的那張表不受影響。
    """
    _require_enabled()
    DEMO_TABLE.clear()
    return {"ok": True, "mapping_entries": len(DEMO_TABLE)}


@router.get("/events")
async def events(since: int = 0) -> dict:
    """自 `since` 之後發生的事件，給「即時監看」分頁輪詢用。

    `since` 帶上前一次拿到的 `last_id`，就只會拿到新的部分 —— 不必每次重傳
    整份緩衝。第一次呼叫用 `since=0` 取得目前緩衝區的全部（最多 100 筆）。

    事件內容**只有型別與數量，沒有任何原始個資**（見 `proxy/traffic.py`）。
    """
    _require_enabled()
    from proxy import traffic

    return {
        "events": traffic.EVENTS.since(since),
        "last_id": traffic.EVENTS.last_id,
        "traffic": traffic.STATS.snapshot(),
    }


@router.get("/status")
async def status(request: Request) -> dict:
    """proxy 目前的設定與**真實**流量狀態（不是示範用的那份）。"""
    _require_enabled()
    from proxy import traffic

    live_table: MappingTable = request.app.state.mapping
    return {
        "upstream_configured": bool(config.UPSTREAM_BASE_URL),
        "upstream": config.UPSTREAM_BASE_URL,
        "model": config.DEFAULT_MODEL,
        "ner_enabled": config.ENABLE_NER,
        "ner_allow_types": sorted(config.NER_ALLOW_TYPES),
        "skip_types": sorted(config.SKIP_TYPES),
        "risk_warning_enabled": config.ENABLE_RISK_WARNING,
        "mapping_idle_timeout": config.MAPPING_IDLE_TIMEOUT,
        "traffic": traffic.STATS.snapshot(),
        "live_mapping_entries": len(live_table),
        "live_mapping_by_type": live_table.issued_counts(),
        "live_cache": detector.CACHE.stats(),
        "demo_mapping_entries": len(DEMO_TABLE),
    }
