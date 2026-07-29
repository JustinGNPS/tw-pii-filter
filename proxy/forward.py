"""把請求原樣轉發給上游 LLM，並把回應（含 SSE 串流）送回 agent。

第一版是**完全透明**的：除了必要的協定層改寫（Host、金鑰、hop-by-hop 標頭），
請求與回應的內容一個位元組都不改。先確定「攔得下來又不會弄壞 agent」，
遮蔽與還原是下一版的事。
"""

import httpx

from proxy import config

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


def upstream_url(path: str) -> str:
    """把 agent 打的路徑接到上游 base URL 後面。"""
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
    """
    request = client.build_request(
        method,
        upstream_url(path),
        params=params,
        headers=build_request_headers(headers),
        content=body if body else None,
    )
    return await client.send(request, stream=True)
