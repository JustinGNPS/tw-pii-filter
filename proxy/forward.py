"""把請求原樣轉發給上游 LLM，並把回應（含 SSE 串流）送回 agent。

第一版是**完全透明**的：除了必要的協定層改寫（Host、金鑰、hop-by-hop 標頭），
請求與回應的內容一個位元組都不改。先確定「攔得下來又不會弄壞 agent」，
遮蔽與還原是下一版的事。
"""

import logging
import ssl

import httpx

from proxy import config

logger = logging.getLogger("proxy")

# 「還沒收到任何回應就斷線」的錯誤：請求的結果還沒有任何部分送回 agent，
# 重送一次不會造成重複輸出。實測（2026-10-25，Claude Code 經 proxy 連校內端點）
# 一個工作階段內出現 3 次 `ssl.SSLError: DECRYPTION_FAILED_OR_BAD_RECORD_MAC`，
# 走不走系統代理都會發生，原因未查明；它不是 httpx.RequestError 的子類別，
# 原本會穿過 main.py 的錯誤處理變成無訊息的 500。
_RETRYABLE_BEFORE_RESPONSE = (ssl.SSLError, httpx.RemoteProtocolError)

# 上游 base URL 未設定時要回給 agent 的訊息。寫成常數是因為
# `proxy/main.py` 的錯誤處理與測試都要引用同一份文字。
MISSING_UPSTREAM_MESSAGE = (
    "上游 base URL 未設定，proxy 不知道要把請求轉發到哪裡。"
    "請在 repo 根目錄的 .env 設定 UPSTREAM_BASE_URL"
    "（例：UPSTREAM_BASE_URL=https://your-llm-gateway.example/v1），"
    "或使用別名 OPENAI_BASE_URL / OPENAI_API_BASE / AIR_BASE_URL。"
)

# hop-by-hop 標頭：只在單一連線上有意義，不可以被 proxy 原樣轉送
# （RFC 9110 §7.6.1）。content-length / accept-encoding 交給 httpx 自己重算。
_DROP_REQUEST_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "proxy-authorization",
    "proxy-authenticate",
    "te",
    "trailer",
    "accept-encoding",
    "authorization",  # 換成 proxy 自己的上游金鑰
    # 其他格式的金鑰標頭也一律丟掉：agent 若填的是真金鑰（例如 Anthropic 的
    # x-api-key、Azure 的 api-key），原樣轉發等於把它交給另一家服務。
    "x-api-key",
    "api-key",
}

_DROP_RESPONSE_HEADERS = {
    "content-length",
    "content-encoding",  # httpx 已幫我們解壓，原標頭會對不上
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "te",
    "trailer",
}


def build_request_headers(incoming: dict[str, str]) -> dict[str, str]:
    """把 agent 送來的標頭整理成可以轉發給上游的版本。

    agent 自己帶的 Authorization 一律丟掉，換成 proxy 從環境變數讀到的上游金鑰
    —— 這樣使用者在 agent 那邊可以填假金鑰，真金鑰只留在 proxy 這一側。
    """
    headers = {
        key: value
        for key, value in incoming.items()
        if key.lower() not in _DROP_REQUEST_HEADERS
    }
    if config.UPSTREAM_API_KEY:
        headers["authorization"] = f"Bearer {config.UPSTREAM_API_KEY}"
    return headers


def filter_response_headers(incoming: dict[str, str]) -> dict[str, str]:
    """把上游回應標頭整理成可以送回 agent 的版本。"""
    return {
        key: value
        for key, value in incoming.items()
        if key.lower() not in _DROP_RESPONSE_HEADERS
    }


class UpstreamNotConfigured(RuntimeError):
    """上游 base URL 沒設定 —— 是部署設定缺漏，不是 agent 送錯請求。

    分成獨立的例外型別，是為了讓 `proxy/main.py` 能把它轉成一則講得清楚的
    錯誤回覆。若讓它自然往上拋，agent 收到的是沒有任何線索的 500。
    """


def upstream_url(path: str) -> str:
    """把 agent 打的路徑接到上游 base URL 後面。

    base URL 沒設定時**必須在這裡擋下來**：接出來的會是 `/v1/chat/completions`
    這種相對路徑，而 httpx 的 `build_request()` 對它不會報錯，要到 `send()`
    才丟 `UnsupportedProtocol`（實測確認）—— 那時例外已經在轉發途中，agent
    只會收到一個沒有任何線索的 500，看不出問題出在自己沒設 `.env`。
    """
    if not config.UPSTREAM_BASE_URL:
        raise UpstreamNotConfigured(MISSING_UPSTREAM_MESSAGE)
    return f"{config.UPSTREAM_BASE_URL}/{path.lstrip('/')}"


def is_event_stream(response: httpx.Response) -> bool:
    """上游是否以 SSE 串流回應。"""
    return "text/event-stream" in response.headers.get("content-type", "").lower()


def make_client() -> httpx.AsyncClient:
    """建立轉發用的 httpx client（在 FastAPI lifespan 中建立、關閉）。"""
    timeout = httpx.Timeout(
        connect=config.CONNECT_TIMEOUT,
        read=config.READ_TIMEOUT,
        write=config.READ_TIMEOUT,
        pool=config.CONNECT_TIMEOUT,
    )
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True)


async def open_upstream(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    params: list[tuple[str, str]] | None,
    headers: dict[str, str],
    body: bytes,
) -> httpx.Response:
    """發出上游請求，回傳**尚未讀取內容**的串流回應。

    一律用 `stream=True`：SSE 要能一段一段往下傳，不能等整包收完
    （否則 agent 那邊的打字機效果會消失，且大回應會卡住）。
    呼叫端負責讀完後 `aclose()`。

    收到回應**之前**連線就斷掉（見 `_RETRYABLE_BEFORE_RESPONSE`）會自動重送一次。
    開始串流之後的斷線不在這裡處理 —— 那時已有內容送到 agent，重送會重複。
    """
    request = client.build_request(
        method,
        upstream_url(path),
        params=params,
        headers=build_request_headers(headers),
        content=body if body else None,
    )
    try:
        return await client.send(request, stream=True)
    except _RETRYABLE_BEFORE_RESPONSE as exc:
        logger.warning("與上游的連線在收到回應前中斷，重送一次：%s", exc)
    try:
        return await client.send(request, stream=True)
    except ssl.SSLError as exc:
        # 轉成 httpx 的連線錯誤，交給 main.py 既有的「上游連不上」處理（502 + 說明）
        raise httpx.ConnectError(f"與上游的加密連線中斷（重送一次仍失敗）：{exc}", request=request) from exc
