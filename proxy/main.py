"""FastAPI 應用：對 agent 假裝自己是一台 OpenAI 相容伺服器。

啟動方式（在 repo 根目錄）：

    .venv\\Scripts\\python.exe -m uvicorn proxy.main:app --port 8000

agent 那邊把 base URL 指到 `http://localhost:8000/v1` 即可。

第一版行為：**完全透明轉發 + 只警告不遮蔽**（依 PDF §7.3）。
偵測到個資時只在 proxy 的 log 印出型別與筆數，請求內容照原樣送給雲端 AI。
"""

import json
import logging
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from proxy import config, detector, forward

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
    logger.info("proxy 啟動\n%s", config.startup_summary())
    if not config.UPSTREAM_API_KEY:
        logger.warning("找不到上游金鑰，轉發一定會失敗。請確認 .env 內的變數名稱。")
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
        "mode": "transparent",  # 第一版：只警告，不遮蔽
    }


def _warn_if_pii(path: str, body: bytes) -> float:
    """掃描請求內容並在 log 印出警告，回傳掃描花費的毫秒數。

    第一版**不修改 body**。偵測或解析失敗一律吞掉 —— proxy 的第一要務是
    不要弄壞 agent，偵測只是附加價值。
    """
    if not body or not any(path.endswith(p) for p in _SCANNED_PATHS):
        return 0.0

    started = time.perf_counter()
    try:
        payload = json.loads(body.decode("utf-8"))
        results = detector.scan_payload(payload)
        warning = detector.format_warning(detector.summarize(results))
        if warning:
            # 只印型別與筆數，絕不印偵測到的原始內容
            logger.warning("%s（%d 個欄位）", warning, len(results))
    except Exception as exc:  # noqa: BLE001 - 偵測失敗不能影響轉發
        logger.debug("略過偵測：%s", exc)
    return (time.perf_counter() - started) * 1000


async def _proxy(path: str, request: Request) -> Response:
    body = await request.body()
    detect_ms = _warn_if_pii(path, body)

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
        # SSE：一段一段往下傳，不能等整包收完，否則 agent 的串流效果會消失
        async def relay():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                total = (time.perf_counter() - started) * 1000
                logger.info(
                    "%s /%s -> %d [SSE] 上游 %.0f ms｜偵測 %.1f ms",
                    request.method,
                    path,
                    upstream.status_code,
                    total,
                    detect_ms,
                )

        return StreamingResponse(
            relay(),
            status_code=upstream.status_code,
            headers=headers,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )

    content = await upstream.aread()
    await upstream.aclose()
    total = (time.perf_counter() - started) * 1000
    logger.info(
        "%s /%s -> %d 上游 %.0f ms｜偵測 %.1f ms",
        request.method,
        path,
        upstream.status_code,
        total,
        detect_ms,
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
