"""遮蔽：把一段文字裡的真實個資換成佔位符。

這裡只有**純字串邏輯** —— 拿一段文字加一組 span，換成佔位符。不知道
OpenAI 的 payload 長什麼樣、不讀任何環境變數，因此兩個載體（proxy 與
瀏覽器擴充）可以共用同一份規則。

payload 層的遮蔽（走訪 JSON 找出該掃的欄位、讀設定、算組合風險）留在
`proxy/masker.py`，那是協定處理，不屬於這裡。

## 為什麼一定要從後往前

替換會改變文字長度。`A123456789`（10 字）換成 `[TW_ID_1]`（9 字）之後，
整段文字短了 1 個字，**後面所有 span 的 start/end 就全部偏掉了**。
由座標大的往小的替換，每次替換只影響已經處理完的部分。

A 的 `detect_all()` 內部已做 Layer 4 重疊仲裁，spans 保證互不重疊，
因此這裡不需要再處理重疊情形。

## 不是每一筆偵測到的東西都該遮

語意層會回傳職稱這類「認得出來、但不是個資」的實體。遮掉它們對隱私沒有
任何幫助，卻會讓 agent 讀不懂上下文。**哪些型別該跳過是政策，不是邏輯**，
所以這裡不設任何預設值：`skip_types=None` 就是「一種都不跳過」。
proxy 那側的預設（`config.SKIP_TYPES`）由 `proxy/masker.py` 注入，
擴充那側自己決定，兩邊不互相牽制。
"""

from collections.abc import Iterable

from core.redact.mapping import MappingTable, normalize_type


def spans_to_mask(spans: list[dict], skip: frozenset[str]) -> list[dict]:
    """濾掉不該遮蔽的型別。"""
    return [span for span in spans if normalize_type(span["type"]) not in skip]


def mask_text(
    text: str,
    spans: list[dict],
    table: MappingTable,
    skip_types: Iterable[str] | None = None,
) -> str:
    """把單一段文字裡的所有 span 換成佔位符（跳過的型別原樣保留）。

    `skip_types=None` 表示不跳過任何型別。呼叫端若有政策上的預設值，
    請自己解析好再傳進來（理由見模組說明）。傳進來的代碼一律正規化，
    否則呼叫端寫小寫的 `position` 會靜默失效。
    """
    skip = frozenset(normalize_type(item) for item in (skip_types or ()))
    kept = sorted(spans_to_mask(spans, skip), key=lambda s: s["start"])

    # 先依「出現順序」配號碼，再從後往前替換。
    # 兩件事要分開做：替換必須倒著來（否則座標偏掉），但發號碼不該跟著倒過來，
    # 不然文章裡第一個出現的人會拿到比較大的號碼。功能上無害，但看的人會困惑。
    tokens = [table.token_for(span["type"], span["text"]) for span in kept]

    for span, token in zip(reversed(kept), reversed(tokens)):
        text = text[: span["start"]] + token + text[span["end"] :]
    return text
