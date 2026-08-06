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
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from proxy import config, detector, forward, masker, restorer
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
    app.state.mapping = MappingTable()
    logger.info("proxy 啟動\n%s", config.startup_summary())
    if not config.UPSTREAM_API_KEY:
        logger.warning("找不到上游金鑰，轉發一定會失敗。請確認 .env 內的變數名稱。")
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
        "detection_cache": detector.CACHE.stats(),
    }


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
        counts = masker.mask_payload(payload, table)
        if counts:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            # 只印型別與筆數，絕不印遮蔽掉的原始內容
            logger.warning(
                "已遮蔽：%s｜快取命中率 %.0f%%",
                detector.format_warning(counts),
                detector.CACHE.hit_rate * 100,
            )
    except json.JSONDecodeError:
        pass  # 不是 JSON（例如檔案上傳），本來就沒得掃
    except Exception as exc:  # noqa: BLE001 - 遮蔽失敗不能讓 agent 拿不到回覆
        logger.error("遮蔽失敗，該次請求未受保護：%s", exc)
    return body, (time.perf_counter() - started) * 1000


async def _proxy(path: str, request: Request) -> Response:
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
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@app.api_route("/v1/{path:path}", methods=_METHODS)
async def proxy_v1(path: str, request: Request) -> Response:
    return await _proxy(path, request)


@app.api_route("/{path:path}", methods=_METHODS)
async def proxy_fallback(path: str, request: Request) -> Response:
    """有些 agent 的 base URL 不帶 `/v1`，這裡一併接住，避免相容性問題。"""
    return await _proxy(path, request)
