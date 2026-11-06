"""只供 Chrome 擴充呼叫的本機語意辨識端點。

瀏覽器擴充本身保留快速的 TypeScript 規則層；需要姓名、地址、公司與職稱時，
把文字送到同一台電腦的 Proxy，由已驗證的 Python NER 執行。請求不會轉發到
上游 AI，回應也不包含整段原文，只回傳合併所需的座標、類型與信心值。

安全邊界：
- 啟動器只把 Proxy 綁在 127.0.0.1。
- 端點再次檢查 TCP 用戶端必須是 loopback，避免有人把 uvicorn 綁到 0.0.0.0。
- 要求自訂標頭；一般網頁無法在沒有成功 CORS 預檢的情況下送出這個標頭。
- 若瀏覽器帶 Origin，只接受 chrome-extension://。
"""

from __future__ import annotations

import asyncio
import ipaddress
import time

from fastapi import APIRouter, HTTPException, Request

from proxy import config, detector

router = APIRouter(prefix="/extension", tags=["extension"])

CLIENT_HEADER = "x-tw-pii-client"
CLIENT_VALUE = "chrome-extension"
MAX_TEXT_CHARS = 20_000


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _require_local_extension(request: Request) -> None:
    if not _is_loopback(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="只接受本機連線")
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(status_code=415, detail="只接受 application/json")
    if request.headers.get(CLIENT_HEADER) != CLIENT_VALUE:
        raise HTTPException(status_code=403, detail="缺少瀏覽器擴充識別標頭")

    origin = request.headers.get("origin")
    if origin is not None and not origin.startswith("chrome-extension://"):
        raise HTTPException(status_code=403, detail="只接受 Chrome 擴充發出的請求")


@router.post("/ner")
async def semantic_scan(request: Request) -> dict:
    """執行本機語意辨識；不遮蔽、不建立對照表，也不接觸上游 API。"""
    _require_local_extension(request)
    if not config.ENABLE_NER:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ner_disabled",
                "message": "本機 Proxy 尚未開啟語意層，請重新啟動並選擇開啟語意辨識。",
            },
        )

    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="JSON 格式錯誤") from exc
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text 必須是字串")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"文字超過語意層上限（{MAX_TEXT_CHARS} 字）",
        )
    if not text.strip():
        return {
            "spans": [],
            "offset_encoding": "unicode-codepoints",
            "timing": {"detect_ms": 0.0},
        }

    started = time.perf_counter()
    spans = await asyncio.to_thread(detector.detect_semantic, text)
    elapsed = (time.perf_counter() - started) * 1000

    # 不回傳 span.text / replacement：擴充本來就持有原文，可依座標重建；
    # 回應保留最小資料面，也避免未來除錯工具把整段個資意外印出來。
    safe_spans = [
        {
            "start": span["start"],
            "end": span["end"],
            "type": normalize_type,
            "confidence": span["confidence"],
            "source": "model",
        }
        for span in spans
        if (normalize_type := str(span.get("type", "")).upper())
    ]
    return {
        "spans": safe_spans,
        # Python 的索引以 Unicode code point 計；JavaScript 以 UTF-16 code unit 計。
        # 擴充看到這個宣告後會轉換，否則 Emoji 前面的 span 會整段錯位。
        "offset_encoding": "unicode-codepoints",
        "timing": {"detect_ms": round(elapsed, 2)},
    }
