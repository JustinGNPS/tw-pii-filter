"""FastAPI 應用：對 agent 假裝自己是一台 OpenAI 相容伺服器。

啟動方式（在 repo 根目錄）：

    .venv\\Scripts\\python.exe -m uvicorn proxy.main:app --port 8000

agent 那邊把 base URL 指到 `http://localhost:8000/v1` 即可。

第二版行為：**遮蔽 + 還原**。
送出前把個資換成佔位符，雲端 AI 全程只看到 `[TW_ID_1]`；
回覆時再換回真值，agent 拿到的內容與沒裝過濾器時一致。

遮蔽與還原必須同時啟用 —— 只遮蔽不還原會讓 agent 的 diff 比對失敗
（實測 Aider 會回報 SEARCH/REPLACE block failed to match）。
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from proxy import (
    anthropic_adapter,
    config,
    demo,
    detector,
    forward,
    masker,
    restorer,
    risk,
    traffic,
)
from proxy.mapping import MappingTable

# Windows 主控台預設是 cp950，中文警告訊息可能會炸掉；統一轉成 utf-8
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("proxy")

# 只有這些路徑值得花時間掃描（其餘如 /v1/models 沒有使用者輸入）
_SCANNED_PATHS = ("chat/completions", "completions", "embeddings", "responses")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = forward.make_client()
    # 對照表只存在記憶體，隨行程結束消失 —— 真實個資不落地
    # 閒置逾時後會自動清空（決定 11），逾時秒數見 config.MAPPING_IDLE_TIMEOUT
    app.state.mapping = MappingTable(idle_timeout=config.MAPPING_IDLE_TIMEOUT)
    logger.info("proxy 啟動\n%s", config.startup_summary())
    if not config.UPSTREAM_BASE_URL:
        logger.warning(forward.MISSING_UPSTREAM_MESSAGE)
    if not config.UPSTREAM_API_KEY:
        logger.warning("找不到上游金鑰，轉發一定會失敗。請確認 .env 內的變數名稱。")
    # agent 沒指到 proxy 時不會有任何跡象（見 proxy/traffic.py），
    # 所以先講明該看什麼，使用者才知道「沒出現那一行」代表什麼
    logger.info(
        "尚未收到任何請求。agent 開始工作後這裡會印一行「第一個請求已抵達 proxy」；"
        "若始終沒出現，代表流量沒有走這裡（agent 的 base URL 沒指過來）。"
    )
    if config.ENABLE_NER:
        # core/ner/detector.py 的 `_get_detector()` 單例目前沒有鎖（C 在 PR #11
        # review 抓到：`asyncio.to_thread` 讓多個請求可能真的並行進 thread pool，
        # 冷啟動時兩個請求可能同時把 BERT 模型各載入一次）。啟動時先跑一次把
        # 單例建好，之後第一個真實請求就不用再付模型載入的錢，也大幅縮小併發
        # 撞上的窗口 —— 但單例本身沒鎖這件事仍待 D 在 core/ner/detector.py 修，
        # 這裡只是治標。
        await asyncio.to_thread(detector._extra_spans, "")
        logger.info("語意層模型已預熱")
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(title="tw-pii-filter proxy", lifespan=lifespan)

# 示範頁面（`PII_ENABLE_DEMO=1` 才會真的回應，否則一律 404）。
# **必須在檔案底部那兩條萬用路由之前註冊** —— FastAPI 依註冊順序比對，
# 晚註冊的話 `/demo` 會先被 `/{path:path}` 接走、當成要轉發給上游的請求。
app.include_router(demo.router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """瀏覽器會自動要 favicon，不要把它轉發給上游。

    沒有這條路由的話，`/{path:path}` 萬用路由會把它當成要轉發的請求送出去 ——
    實測是一次 1367 ms 的往返只為了拿回 404，而且會被計入「agent 流量」，
    讓那個判準（見 `proxy/traffic.py`）失真。開了 `/demo` 頁面之後每次載入
    都會發生一次。
    """
    return Response(status_code=204)


@app.exception_handler(forward.UpstreamNotConfigured)
async def _upstream_not_configured(
    request: Request, exc: forward.UpstreamNotConfigured
) -> Response:
    """沒設定上游 base URL 時，回一則講得清楚的錯誤給 agent。

    掛成 app 層級的處理器（而不是在每個路由各包一個 try），是因為轉發有
    **兩條路徑**：`_proxy()` 與 Anthropic 相容路由，兩邊都會呼叫
    `forward.open_upstream()`。掛在這裡一次涵蓋，日後多一條路徑也不會漏。

    用 502（Bad Gateway）而不是 500：問題出在「proxy 與上游之間」而不是
    agent 送來的請求。body 用 OpenAI 的錯誤格式包，agent 才顯示得出訊息 ——
    多數 OpenAI 相容客戶端只認 `error.message` 這個欄位。
    """
    logger.error("%s", exc)
    return JSONResponse(
        status_code=502,
        content={
            "error": {
                "message": str(exc),
                "type": "proxy_configuration_error",
                "code": "upstream_not_configured",
            }
        },
    )


@app.get("/healthz")
async def healthz() -> dict:
    """本地健康檢查，不會轉發到上游。"""
    return {
        "status": "ok",
        "upstream": config.UPSTREAM_BASE_URL,
        "upstream_key_loaded": bool(config.UPSTREAM_API_KEY),
        "mode": "masking",  # 第二版：遮蔽 + 還原
        "ner_enabled": config.ENABLE_NER,
        "mapping_entries": len(app.state.mapping),
        # 累計：每個型別看過幾個不重複的真值。log 只印「本輪新增」，
        # 想知道整體狀況看這裡（見 _log_new_values）
        "mapping_by_type": app.state.mapping.issued_counts(),
        "detection_cache": detector.CACHE.stats(),
        "traffic": traffic.STATS.snapshot(),
    }


def _record_arrival(method: str, path: str) -> None:
    """記一次「請求真的抵達 proxy」，第一次額外印一行。

    只在轉發路徑上呼叫，`/healthz` 不算 —— 使用者自己 curl 健康檢查不代表
    agent 走了 proxy，把它算進去會讓這個數字失去意義。
    """
    if traffic.STATS.record():
        logger.info(
            "第一個請求已抵達 proxy（%s /%s）—— 流量確實有走這裡",
            method,
            path.lstrip("/"),
        )


def _log_new_values(before: dict[str, int], table: MappingTable, tag: str = "") -> None:
    """印出「這一輪新增的個資」，沒有新增就**完全不印**。

    為什麼不印「這包 payload 遮了幾筆」：agent 每輪都重送整段對話歷史，同一批
    個資每輪都會被重新掃到，那個數字會隨對話變長一路往上爬（實測一次 Codex
    工作階段：6 -> 8 -> 14 -> 17，而後面三輪根本沒有任何新的個資）。使用者
    真正要知道的是「**又有沒看過的個資送出去了**」，不是「歷史裡總共有幾筆」。

    沒有新增就靜默，是因為那種情況沒有任何新資訊 —— 該輪仍然有一行
    `POST /... -> 200` 的完成紀錄可以證明 proxy 有在工作，累計狀況則看
    `/healthz` 的 `mapping_by_type`。

    只印型別與數量，絕不印遮蔽掉的原始內容。
    """
    new = masker.new_value_counts(before, table.issued_counts())
    if not new:
        return
    logger.warning(
        "已遮蔽%s：%s｜快取命中率 %.0f%%",
        tag,
        detector.format_new_values(new),
        detector.CACHE.hit_rate * 100,
    )
    # 只記型別與數量，絕不記原始內容（見 proxy/traffic.py 的紅線說明）
    traffic.EVENTS.record(
        "mask",
        counts=new,
        total=sum(new.values()),
        cache_hit_rate=round(detector.CACHE.hit_rate, 3),
        tag=tag,
    )


def _log_combination_risk(combination_risk: dict | None) -> None:
    """達到門檻就印一行組合風險警示（兩條遮蔽路徑共用）。

    刻意跟「已遮蔽 N 筆」分開印、也分開判斷：這兩件事沒有連動關係。一段
    「35 歲女性資深工程師」可能一筆個資都沒遮到，卻是這包 payload 裡風險
    最高的內容；反過來遮了 90 筆的請求也可能組合風險為零。
    """
    if combination_risk and risk.is_warning_worthy(combination_risk):
        logger.warning(risk.format_warning(combination_risk))


def _mask_request(path: str, body: bytes, table: MappingTable) -> tuple[bytes, float]:
    """遮蔽請求內容，回傳 (要送出的 body, 花費的毫秒數)。

    這是同步函式，由 `_proxy` 透過 `asyncio.to_thread` 丟到 thread pool 執行，
    不佔用 event loop。純規則層時這筆成本只有幾毫秒，感覺不出差別；但
    `PII_ENABLE_NER=1` 開啟語意層後單次偵測約 742 ms（CPU），若直接在
    event loop 裡同步跑，會擋住同一個 proxy 行程裡的其他請求排隊等它。

    遮蔽或解析失敗一律吞掉並原樣轉發 —— proxy 的第一要務是不要弄壞 agent。
    代價是那次請求不受保護，因此失敗會以 ERROR 等級留下紀錄。
    """
    if not body or not any(path.endswith(p) for p in _SCANNED_PATHS):
        return body, 0.0

    started = time.perf_counter()
    try:
        payload = json.loads(body.decode("utf-8"))
        issued_before = table.issued_counts()
        counts, combination_risk = masker.mask_payload_with_risk(payload, table)
        if counts:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        _log_new_values(issued_before, table)
        _log_combination_risk(combination_risk)
    except json.JSONDecodeError:
        pass  # 不是 JSON（例如檔案上傳），本來就沒得掃
    except Exception as exc:  # noqa: BLE001 - 遮蔽失敗不能讓 agent 拿不到回覆
        logger.error("遮蔽失敗，該次請求未受保護：%s", exc)
    return body, (time.perf_counter() - started) * 1000


async def _proxy(path: str, request: Request) -> Response:
    _record_arrival(request.method, path)
    table: MappingTable = request.app.state.mapping
    body = await request.body()
    body, detect_ms = await asyncio.to_thread(_mask_request, path, body, table)

    started = time.perf_counter()
    upstream = await forward.open_upstream(
        request.app.state.client,
        request.method,
        path,
        list(request.query_params.multi_items()),
        dict(request.headers),
        body,
    )
    headers = forward.filter_response_headers(dict(upstream.headers))

    if forward.is_event_stream(upstream):
        # SSE：一段一段往下傳，不能等整包收完，否則 agent 的串流效果會消失。
        # 佔位符可能被切在兩個 chunk 或兩個事件之間，交給 SSERestorer 處理。
        async def relay():
            sse = restorer.SSERestorer(table)
            try:
                async for chunk in upstream.aiter_bytes():
                    if not len(table):
                        # 對照表是空的就沒有東西需要還原，原樣穿透即可，
                        # 省下重新序列化的成本，也維持位元組層級的透明
                        yield chunk
                        continue
                    out = sse.feed(chunk)
                    if out:
                        yield out
                tail = sse.flush()
                if tail:
                    yield tail
            finally:
                await upstream.aclose()
                total = (time.perf_counter() - started) * 1000
                logger.info(
                    "%s /%s -> %d [SSE] 上游 %.0f ms｜遮蔽 %.1f ms｜還原 %d 筆%s",
                    request.method,
                    path,
                    upstream.status_code,
                    total,
                    detect_ms,
                    sse.restored,
                    f"（{sse.unknown} 筆查無對照）" if sse.unknown else "",
                )
                traffic.EVENTS.record(
                    "done",
                    method=request.method,
                    path=path,
                    status=upstream.status_code,
                    stream=True,
                    upstream_ms=round(total),
                    detect_ms=round(detect_ms, 1),
                    restored=sse.restored,
                    unknown=sse.unknown,
                )

        return StreamingResponse(
            relay(),
            status_code=upstream.status_code,
            headers=headers,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )

    content = await upstream.aread()
    await upstream.aclose()

    restored = unknown = 0
    if len(table):  # 對照表是空的就沒有東西需要還原
        content, restored, unknown = restorer.restore_body(content, table)

    total = (time.perf_counter() - started) * 1000
    logger.info(
        "%s /%s -> %d 上游 %.0f ms｜遮蔽 %.1f ms｜還原 %d 筆%s",
        request.method,
        path,
        upstream.status_code,
        total,
        detect_ms,
        restored,
        f"（{unknown} 筆查無對照）" if unknown else "",
    )
    traffic.EVENTS.record(
        "done",
        method=request.method,
        path=path,
        status=upstream.status_code,
        stream=False,
        upstream_ms=round(total),
        detect_ms=round(detect_ms, 1),
        restored=restored,
        unknown=unknown,
    )
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]


# ---------------------------------------------------------------------------
# Claude Code 相容性：7 步計畫第 3 步「純文字最小遮蔽」+ 第 4 步「工具呼叫
# 遞迴處理」。
#
# AIR 不支援 Anthropic Messages API，所以這條路徑比其他 agent 多一層格式
# 翻譯：Anthropic 請求 -> 遮蔽（沿用既有 masker，見 anthropic_adapter 模組
# docstring）-> 翻成 OpenAI 相容格式送給 AIR（含 tools/tool_calls/tool_result
# 的轉換）-> 用既有 restorer 還原 -> 把還原後的內容包回 Anthropic 的串流
# 事件格式（純文字或 tool_use block）。
#
# 範圍仍然限縮：圖片／文件附件（`image`/`document` block）還不會翻譯，
# 遇到就誠實回報「還沒支援」，不要硬翻出來讓 Claude Code 收到看似正常、
# 實際上亂掉的回覆。
# ---------------------------------------------------------------------------


def _anthropic_error(status_code: int, error_type: str, message: str) -> Response:
    return Response(
        content=json.dumps(
            {"type": "error", "error": {"type": error_type, "message": message}},
            ensure_ascii=False,
        ),
        status_code=status_code,
        media_type="application/json",
    )


def _mask_anthropic_payload(payload: dict, table: MappingTable) -> tuple[dict, float]:
    """遮蔽 Anthropic 格式的請求 payload，回傳 (原 payload，花費的毫秒數)。

    與 `_mask_request` 對稱：同步函式、丟到 thread pool 執行、失敗一律吞掉
    原樣放行——proxy 的第一要務是不能弄壞 agent，這條路徑也不例外。
    """
    started = time.perf_counter()
    try:
        issued_before = table.issued_counts()
        _, combination_risk = masker.mask_payload_with_risk(payload, table)
        _log_new_values(issued_before, table, tag="（Claude Code）")
        _log_combination_risk(combination_risk)
    except Exception as exc:  # noqa: BLE001 - 遮蔽失敗不能讓 agent 拿不到回覆
        logger.error("遮蔽失敗，該次請求未受保護：%s", exc)
    return payload, (time.perf_counter() - started) * 1000


async def _proxy_anthropic(request: Request) -> Response:
    table: MappingTable = request.app.state.mapping
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None

    if not isinstance(payload, dict):
        return _anthropic_error(400, "invalid_request_error", "無法解析請求內容")

    if anthropic_adapter.has_unsupported_content(payload):
        return _anthropic_error(
            501,
            "not_implemented",
            "此 proxy 尚未支援圖片／文件附件內容（純文字對話與工具呼叫已支援，"
            "開發中，見 docs/B_progress.md）",
        )

    started = time.perf_counter()
    payload, detect_ms = await asyncio.to_thread(_mask_anthropic_payload, payload, table)

    model = payload.get("model") or config.DEFAULT_MODEL
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    # Claude Code 一律送 stream: true；stream 欄位預設當 true 處理，
    # 保留 False 分支是為了不排除其他可能不用串流的呼叫端。
    wants_stream = payload.get("stream", True) is not False

    openai_request = anthropic_adapter.to_openai_request(payload, model=config.DEFAULT_MODEL)
    openai_request["stream"] = wants_stream
    openai_body = json.dumps(openai_request, ensure_ascii=False).encode("utf-8")

    upstream = await forward.open_upstream(
        request.app.state.client,
        "POST",
        "chat/completions",
        None,
        {"content-type": "application/json"},
        openai_body,
    )

    if wants_stream:
        return await _relay_anthropic_stream(
            upstream, table, started, detect_ms, model, message_id
        )
    return await _relay_anthropic_once(upstream, table, started, detect_ms, model, message_id)


async def _relay_anthropic_once(
    upstream, table: MappingTable, started: float, detect_ms: float, model: str, message_id: str
) -> Response:
    """非串流路徑：等 AIR 回完整結果，還原後一次包成單一 SSE 事件送出。

    第 3/4 步當時的做法，保留給 `stream: false` 的請求（Claude Code 實際上
    不會這樣送，但不排除其他呼叫端）。真正被 Claude Code 用到的是
    `_relay_anthropic_stream()`（第 6 步：真串流）。
    """
    content = await upstream.aread()
    await upstream.aclose()

    if upstream.status_code >= 400:
        logger.error(
            "Claude Code 轉換後的請求被上游拒絕：%d %s",
            upstream.status_code,
            content[:200],
        )
        return _anthropic_error(
            502, "upstream_error", f"上游拒絕請求（{upstream.status_code}）"
        )

    restored = unknown = 0
    if len(table):
        content, restored, unknown = restorer.restore_body(content, table)

    try:
        openai_response = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.error("無法解析上游回覆：%s", exc)
        return _anthropic_error(502, "upstream_error", "上游回覆格式無法解析")

    total = (time.perf_counter() - started) * 1000
    logger.info(
        "POST /messages（Claude Code）-> %d 上游 %.0f ms｜遮蔽 %.1f ms｜還原 %d 筆%s",
        upstream.status_code,
        total,
        detect_ms,
        restored,
        f"（{unknown} 筆查無對照）" if unknown else "",
    )
    traffic.EVENTS.record(
        "done",
        method="POST",
        path="v1/messages",
        status=upstream.status_code,
        stream=False,
        upstream_ms=round(total),
        detect_ms=round(detect_ms, 1),
        restored=restored,
        unknown=unknown,
    )

    sse = anthropic_adapter.response_to_event_stream(
        openai_response, model=model, message_id=message_id
    )
    return StreamingResponse(iter([sse]), media_type="text/event-stream")


async def _relay_anthropic_stream(
    upstream, table: MappingTable, started: float, detect_ms: float, model: str, message_id: str
) -> Response:
    """第 6 步：真串流。AIR 也用 `stream: true` 呼叫，邊收 OpenAI 格式的
    SSE delta，邊即時翻譯成 Anthropic 格式的事件往外送，不用等完整回覆。

    還原沿用既有的 `restorer.SSERestorer`（第 5 步已驗證過的機制，這裡是
    它第一次真的被 Claude Code 這條路徑使用，先前第 3/4 步走的是非串流的
    `restore_body()`）；格式翻譯交給新寫的 `AnthropicStreamTranslator`
    （`proxy/anthropic_adapter.py`），兩者刻意分開、各司其職。
    """
    if upstream.status_code >= 400:
        content = await upstream.aread()
        await upstream.aclose()
        logger.error(
            "Claude Code 轉換後的請求被上游拒絕：%d %s",
            upstream.status_code,
            content[:200],
        )
        return _anthropic_error(
            502, "upstream_error", f"上游拒絕請求（{upstream.status_code}）"
        )

    async def relay():
        sse_restorer = restorer.SSERestorer(table)
        translator = anthropic_adapter.AnthropicStreamTranslator(
            model=model, message_id=message_id
        )
        try:
            async for chunk in upstream.aiter_bytes():
                restored_chunk = sse_restorer.feed(chunk)
                out = translator.feed(restored_chunk)
                if out:
                    yield out
            tail = sse_restorer.flush()
            out = translator.feed(tail) if tail else b""
            out += translator.flush()
            if out:
                yield out
        finally:
            await upstream.aclose()
            total = (time.perf_counter() - started) * 1000
            logger.info(
                "POST /messages（Claude Code）[SSE] -> %d 上游 %.0f ms｜遮蔽 %.1f ms｜還原 %d 筆%s",
                upstream.status_code,
                total,
                detect_ms,
                sse_restorer.restored,
                f"（{sse_restorer.unknown} 筆查無對照）" if sse_restorer.unknown else "",
            )
            traffic.EVENTS.record(
                "done",
                method="POST",
                path="v1/messages",
                status=upstream.status_code,
                stream=True,
                upstream_ms=round(total),
                detect_ms=round(detect_ms, 1),
                restored=sse_restorer.restored,
                unknown=sse_restorer.unknown,
            )

    return StreamingResponse(relay(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Claude Code 相容性開發用：暫時的 capture 模式（PII_CAPTURE_ANTHROPIC=1）。
#
# AIR 上游不支援 Anthropic Messages API（實測 POST /v1/messages 回 404
# unsupported_endpoint，見 docs/B_progress.md），要做協定轉換前得先看 Claude
# Code 真的送出什麼格式，不要照文件猜 —— 上次測 OpenCode 的教訓是文件沒提到
# 的細節（tool_calls 沒被還原）才是真正會咬人的地方。
#
# 第一版只回 501，結果 CLI 收到錯誤就無限重送同一輪，永遠停在第一輪、看不到
# tool_result 長什麼樣（跟 OpenCode 當初出事的正是這條路徑）。這版改成回一個
# 偽造的 SSE，誘導 CLI 呼叫 Read 工具讀一個無害的測試檔，讓它真的執行、真的
# 送出下一輪帶 tool_result 的請求——藉此在不用先寫完整協定轉換的情況下，
# 把 tool_result 的真實格式也擷取下來。
#
# 這整段只記錄、不轉發、不做任何真正的協定轉換，寫好轉換層之後應該整段刪除。
_CAPTURE_ANTHROPIC = os.getenv("PII_CAPTURE_ANTHROPIC", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
_CAPTURE_DIR = Path(
    os.getenv("PII_CAPTURE_DIR", r"D:\專題(new)\agent-tests\claude-code\captures")
)
_CAPTURE_TARGET_FILE = os.getenv(
    "PII_CAPTURE_TARGET_FILE",
    r"D:\專題(new)\agent-tests\claude-code\customer_export.py",
)


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _fake_sse_tool_use(file_path: str) -> str:
    """偽造一個呼叫 Read 工具的串流回覆，誘導 CLI 送出下一輪 tool_result。"""
    msg_id = f"msg_capture_{uuid.uuid4().hex[:24]}"
    tool_id = f"toolu_capture_{uuid.uuid4().hex[:24]}"
    partial_json = json.dumps({"file_path": file_path}, ensure_ascii=False)

    out = _sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-sonnet-5",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        },
    )
    out += _sse_event(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": tool_id,
                "name": "Read",
                "input": {},
            },
        },
    )
    out += _sse_event(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": partial_json},
        },
    )
    out += _sse_event(
        "content_block_stop", {"type": "content_block_stop", "index": 0}
    )
    out += _sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
    )
    out += _sse_event("message_stop", {"type": "message_stop"})
    return out


def _fake_sse_text(text: str) -> str:
    """偽造一個純文字、end_turn 收尾的串流回覆，讓 CLI 停止重試該輪。

    事件格式跟真正的 `_proxy_anthropic()` 送回去的一樣，直接借用
    `anthropic_adapter.text_event_stream()`，capture 模式只是餵假文字進去。
    """
    return anthropic_adapter.text_event_stream(
        text, model="claude-sonnet-5", message_id=f"msg_capture_{uuid.uuid4().hex[:24]}"
    )


def _has_tool_result(payload: dict) -> bool:
    for message in payload.get("messages", []):
        content = message.get("content")
        if isinstance(content, list) and any(
            block.get("type") == "tool_result" for block in content
        ):
            return True
    return False


@app.post("/v1/messages")
async def anthropic_messages(request: Request) -> Response:
    _record_arrival("POST", "v1/messages")
    if _CAPTURE_ANTHROPIC:
        return await _capture_anthropic_messages(request)
    return await _proxy_anthropic(request)


async def _capture_anthropic_messages(request: Request) -> Response:
    body = await request.body()
    _CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(body.decode("utf-8")) if body else {}
    record = {"headers": dict(request.headers), "body": payload}
    # 偽造回覆幾乎零延遲，同一句任務的前後兩輪常常落在同一秒內送達；
    # 檔名只到秒會讓後一輪蓋掉前一輪，補上隨機尾碼避免撞名遺失擷取樣本
    out_path = _CAPTURE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.json"
    out_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("已擷取 Claude Code 請求 -> %s", out_path)

    tools = payload.get("tools") or []
    if tools and not _has_tool_result(payload):
        # 第一輪、CLI 有宣告工具：誘導呼叫 Read，換取下一輪的 tool_result 樣本
        sse = _fake_sse_tool_use(_CAPTURE_TARGET_FILE)
    elif tools:
        # 已經收到 tool_result 了，正常收尾，不要再誘導下一次工具呼叫
        sse = _fake_sse_text("（capture 模式：已取得 tool_result 樣本，結束回合）")
    else:
        # 無工具的輔助請求（例如 CLI 產生對話標題），給個合理內容讓它別一直重試
        fmt = (payload.get("output_config") or {}).get("format") or {}
        if fmt.get("type") == "json_schema":
            text = json.dumps({"title": "Claude Code capture 測試"}, ensure_ascii=False)
        else:
            text = "(capture-only mode)"
        sse = _fake_sse_text(text)

    return StreamingResponse(iter([sse]), media_type="text/event-stream")


@app.api_route("/v1/{path:path}", methods=_METHODS)
async def proxy_v1(path: str, request: Request) -> Response:
    return await _proxy(path, request)


@app.api_route("/{path:path}", methods=_METHODS)
async def proxy_fallback(path: str, request: Request) -> Response:
    """有些 agent 的 base URL 不帶 `/v1`，這裡一併接住，避免相容性問題。"""
    return await _proxy(path, request)
