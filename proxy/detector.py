"""包住 A 的 `core.rules.detect_all()`，並負責「從 payload 挖出該掃的欄位」。

分成兩件事：

1. **介面隔離**：A 的 `docs/interface.md` 尚未完全定案，proxy 其他檔案一律
   只呼叫本模組，介面若變動只需要改這裡。
2. **欄位萃取**：LLM 請求是一包巢狀 JSON，個資可能藏在 `messages[i].content`、
   content parts、tool call 參數等位置。本模組把這些文字欄位連同「路徑」一起
   取出，讓第二版可以照同一條路徑把遮蔽後的文字寫回去。

**第一版只警告、不改文字**（依 PDF §7.3）。遮蔽與還原是下一版的事。

隱私原則：本模組的回傳與 log 一律**不含偵測到的原始個資內容**，只有型別、
數量與位置。log 檔本身也是個資外洩管道。
"""

from typing import Any

from core.rules import detect_all as _detect_all
from proxy import config
from proxy.cache import DetectionCache
from core.redact.mapping import normalize_type

# JSON 路徑：dict 的 key 用 str，list 的 index 用 int
Path = tuple[Any, ...]

# 行程共用的偵測快取。agent 每次請求都重送整段對話歷史，同一份檔案內容
# 會被重複掃十幾次 —— 見 `proxy/cache.py` 的說明。
CACHE = DetectionCache()


def _extra_spans(text: str) -> list[dict] | None:
    """語意層（D 的 NER）掃描，供 `detect_all(text, extra_spans=...)` 使用。

    `PII_ENABLE_NER` 關閉時（預設）直接回傳 `None`，等同不跑語意層 ——
    語意層單次推論約 742 ms（CPU），是規則層的一百多倍，不該讓每個請求
    都白白付這筆延遲，見 `proxy/README.md`「語意層」一節。

    `core.ner.detector` 內部會 `import torch` / `transformers`，這兩個套件
    很重（GB 等級）。關閉語意層時完全不 import 這個模組，使用者不需要
    安裝這兩個套件也能跑純規則層的 proxy。
    """
    if not config.ENABLE_NER:
        return None
    from core.ner.detector import detect_ner

    return _keep_allowed_types(detect_ner(text))


def _keep_allowed_types(spans: list[dict] | None) -> list[dict] | None:
    """只留下 `config.NER_ALLOW_TYPES` 裡的語意層 spans，其餘直接丟棄。

    語意層用的是**通用領域**中文 NER 模型（14 種標籤，含書名／電影／遊戲／
    場景），拿去掃 agent 送來的英文技術文字時會產生大量雜訊 —— 實測 Claude
    Code 的 system prompt 被判出 `GAME`（連一個反引號都算）與 `ORGANIZATION`。
    理由與白名單的完整實測數據見 `proxy/config.py` 的 `NER_ALLOW_TYPES` 一節。

    **為什麼在這裡丟、而不是在遮蔽時跳過**：這裡是 `detect_all(extra_spans=...)`
    的**上游**，丟掉的 span 從一開始就不存在，因此
      - 不會參與 A 的 Layer 4 重疊仲裁（雜訊 span 不會有機會擠掉真正的規則層結果）
      - 不會被算進 log 的「偵測到 N 筆敏感資訊」（否則數字會被雜訊灌水，
        使用者無從得知其中幾筆是真的）
      - 不會被算進 Layer 3 組合風險分數
    這三件事都是「當作沒偵測到」的應有語意。想要「偵測到但不遮蔽」（例如
    `POSITION`）請用 `config.SKIP_TYPES`，兩者差別見 `proxy/config.py`。

    空的白名單代表**全部採信**（回到改動前的行為），供比對與除錯用。
    """
    if spans is None or not config.NER_ALLOW_TYPES:
        return spans
    return [s for s in spans if normalize_type(s.get("type")) in config.NER_ALLOW_TYPES]


def detect(text: str, cache: DetectionCache | None = None) -> dict:
    """呼叫 A 的偵測核心（經過快取）。回傳格式見 `docs/interface.md`。

    規則層與語意層（若啟用）各自獨立掃描同一段文字，結果一起交給
    `detect_all(text, extra_spans=...)`。A 的 `detect_all()` 內部已做
    Layer 4 重疊仲裁（`core/rules/conflict_resolver.py`），因此回傳的
    spans 保證互不重疊 —— proxy 之後做替換時不需要自己再仲裁。

    偵測是純函式（同樣的文字、同樣的 `PII_ENABLE_NER` 設定下永遠得到同樣的
    結果），因此快取不會改變行為。測試可傳入自己的 `DetectionCache` 以取得隔離。

    `PII_ENABLE_NER` 併入快取 key（見 `DetectionCache.fingerprint` 的
    `key_extra`）：同一份文字在語意層開/關兩種設定下該有不同結果，若只用文字
    當 key，設定切換後可能誤用切換前算出的快取結果。
    """
    table = CACHE if cache is None else cache
    spans = table.get_or_compute(
        text,
        lambda t: _detect_all(t, extra_spans=_extra_spans(t))["spans"],
        key_extra=str(config.ENABLE_NER),
    )
    return {"text": text, "spans": spans}


def _extract_from_block_list(blocks: Any, base: Path) -> list[tuple[Path, str]]:
    """從 `[{"type": "text", "text": "..."}, ...]` 這種 block 陣列取出文字欄位。

    OpenAI 多模態 content parts 與 Anthropic 的 `content`/`system` 都是這個
    形狀，因此共用同一段邏輯——不特別檢查 `type`，只要 block 上有字串型別的
    `text` 欄位就收，跟原本 `_extract_from_message` 的行為一致。
    """
    found: list[tuple[Path, str]] = []
    if not isinstance(blocks, list):
        return found
    for i, part in enumerate(blocks):
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            found.append((base + (i, "text"), part["text"]))
    return found


def _extract_strings(value: Any, base: Path) -> list[tuple[Path, str]]:
    """遞迴走訪任意巢狀 dict/list，找出每個字串葉節點。

    用在 Anthropic 的 `tool_use.input`——工具參數是任意深度的 JSON 物件，
    不像 OpenAI 的 `function.arguments` 是單一字串可以整段當文字掃，
    必須逐個字串欄位掃過去，才能找到（並在遮蔽時寫回）藏在深處的個資。
    """
    found: list[tuple[Path, str]] = []
    if isinstance(value, str):
        found.append((base, value))
    elif isinstance(value, dict):
        for key, sub in value.items():
            found.extend(_extract_strings(sub, base + (key,)))
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            found.extend(_extract_strings(sub, base + (i,)))
    return found


def _extract_from_responses_item(item: Any, base: Path) -> list[tuple[Path, str]]:
    """從 Responses API（`POST /v1/responses`）`input` 陣列的單一項目取出文字。

    Codex CLI 走的是這個端點，payload 形狀跟 Chat Completions 完全不同——
    `input` 不是字串陣列而是**物件陣列**，實測抓到三種型態：

    | `type` | 文字在哪 | 是什麼 |
    |---|---|---|
    | `message` | `content[].text` | 對話訊息（block 的 `type` 是 `input_text`／`output_text`）|
    | `function_call` | `arguments` | 工具呼叫參數（JSON 字串）|
    | `function_call_output` | `output` | 工具執行結果——**agent 讀到的檔案內容在這裡** |

    後兩者跟 Anthropic 的 `tool_use.input`／`tool_result` 是同一個教訓：agent
    每輪重送整段對話歷史，還原過的真值會**再次**出現在下一輪請求裡，所以這兩個
    欄位每輪都必須重新掃描，不能只掃最新的使用者訊息。

    **刻意不檢查 `type` 字串**，只看欄位在不在——理由同
    `_extract_from_block_list()`：Codex 改版時多半是新增項目型態，只要文字仍然
    放在 `content`／`arguments`／`output`，這裡就還吃得下，不會靜默漏掃。
    """
    found: list[tuple[Path, str]] = []
    if not isinstance(item, dict):
        return found

    content = item.get("content")
    if isinstance(content, str):
        found.append((base + ("content",), content))
    elif isinstance(content, list):
        found.extend(_extract_from_block_list(content, base + ("content",)))

    for key in ("arguments", "output"):
        value = item.get(key)
        if isinstance(value, str):
            found.append((base + (key,), value))
        elif isinstance(value, list):
            # 實測到的 `output` 都是字串，但規格允許 block 陣列（例如工具回傳
            # 多段內容）。多這一行，之後 Codex 換形狀時不會又變成靜默漏掃。
            found.extend(_extract_from_block_list(value, base + (key,)))

    return found


def _extract_from_message(message: Any, base: Path) -> list[tuple[Path, str]]:
    """從單一 message 取出所有文字欄位。"""
    found: list[tuple[Path, str]] = []
    if not isinstance(message, dict):
        return found

    content = message.get("content")
    if isinstance(content, str):
        found.append((base + ("content",), content))
    elif isinstance(content, list):
        # 多模態格式：content 是 [{"type": "text", "text": "..."}, ...]
        found.extend(_extract_from_block_list(content, base + ("content",)))

        # Anthropic 專用的兩種 block：
        # - tool_use.input 是 AI 決定呼叫工具時的參數，一旦被還原過就帶著
        #   真值，重送歷史時必須重新掃描（見 anthropic_adapter 模組 docstring）
        # - tool_result.content 是工具實際執行的結果（例如讀檔內容），
        #   字串或文字 block 陣列都要收
        for i, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                found.extend(
                    _extract_strings(block["input"], base + ("content", i, "input"))
                )
            elif block.get("type") == "tool_result":
                tr_content = block.get("content")
                tr_base = base + ("content", i, "content")
                if isinstance(tr_content, str):
                    found.append((tr_base, tr_content))
                elif isinstance(tr_content, list):
                    found.extend(_extract_from_block_list(tr_content, tr_base))

    # agent 讀檔的結果常常是以 tool call 參數的形式回到對話歷史裡
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for i, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            arguments = call.get("function", {}).get("arguments")
            if isinstance(arguments, str):
                found.append(
                    (base + ("tool_calls", i, "function", "arguments"), arguments)
                )

    return found


def extract_texts(payload: Any) -> list[tuple[Path, str]]:
    """從 OpenAI 相容的請求 payload 中，取出所有應該被掃描的文字欄位。

    回傳 `[(路徑, 文字), ...]`，路徑可餵給 `get_at()` / `set_at()`。

    涵蓋範圍：
    - `messages[i].content`（字串或多模態 parts）
    - `messages[i].tool_calls[j].function.arguments`
    - `prompt`（舊版 completions，字串或字串陣列）
    - `input`（embeddings 的字串／字串陣列，**以及 Responses API 的物件陣列**，
      見 `_extract_from_responses_item()`）
    - `system`（Anthropic Messages API 專用欄位，字串或 block 陣列；
      OpenAI 格式的請求不會有這個欄位，此處新增不影響既有行為）
    - `instructions`（Responses API 專用的系統提示欄位）

    **這個函式漏掉一個欄位的後果是「靜默不遮蔽」**——真值原樣送給雲端，而 log
    只會顯示「偵測到 0 筆」，看起來跟「這次剛好沒個資」一模一樣。Codex 的
    `instructions`／`input` 物件陣列就是這樣漏了整整一版（見
    `docs/B_design.md`）。新增 agent 支援時，永遠要拿真實 capture 驗證
    「抓到的字元數 > 0」，不能只看 proxy 沒報錯。
    """
    found: list[tuple[Path, str]] = []
    if not isinstance(payload, dict):
        return found

    messages = payload.get("messages")
    if isinstance(messages, list):
        for i, message in enumerate(messages):
            found.extend(_extract_from_message(message, ("messages", i)))

    system = payload.get("system")
    if isinstance(system, str):
        found.append((("system",), system))
    elif isinstance(system, list):
        found.extend(_extract_from_block_list(system, ("system",)))

    # Responses API 的系統提示欄位（Codex 實測 21,026 字元的系統提示就放這裡）。
    # Chat Completions／Anthropic 格式的請求沒有這個欄位，新增不影響既有行為。
    instructions = payload.get("instructions")
    if isinstance(instructions, str):
        found.append((("instructions",), instructions))

    for key in ("prompt", "input"):
        value = payload.get(key)
        if isinstance(value, str):
            found.append(((key,), value))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str):
                    # embeddings 的字串陣列（本欄位最早就是為它寫的）
                    found.append(((key, i), item))
                else:
                    # Responses API 的物件陣列（Codex）。`prompt` 實務上不會是
                    # 物件陣列，但兩個欄位共用同一段邏輯不會有副作用。
                    found.extend(_extract_from_responses_item(item, (key, i)))

    return found


def get_at(payload: Any, path: Path) -> Any:
    """依路徑取值。"""
    node = payload
    for key in path:
        node = node[key]
    return node


def set_at(payload: Any, path: Path, value: Any) -> None:
    """依路徑寫值（就地修改）。第二版做遮蔽時會用到。"""
    node = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def scan_payload(payload: Any, cache: DetectionCache | None = None) -> list[dict]:
    """掃描整包 payload，回傳每個文字欄位的偵測結果。

    回傳 `[{"path": Path, "text": str, "spans": [...]}, ...]`，只包含有偵測到
    東西的欄位。本函式不修改 payload；遮蔽由 `proxy.masker` 負責。
    """
    results = []
    for path, text in extract_texts(payload):
        spans = detect(text, cache)["spans"]
        if spans:
            results.append({"path": path, "text": text, "spans": spans})
    return results


def summarize(results: list[dict]) -> dict[str, int]:
    """把掃描結果壓成「型別 -> 筆數」，**不含任何原始個資內容**，可安全寫進 log。"""
    counts: dict[str, int] = {}
    for result in results:
        for span in result["spans"]:
            counts[span["type"]] = counts.get(span["type"], 0) + 1
    return counts


def format_new_values(counts: dict[str, int]) -> str:
    """把「本輪新增的不重複真值」格式化成一行。無新增時回傳空字串。

    與 `format_warning()` 分開是因為兩者數的是不同的東西：那邊是「這包
    payload 裡遮掉幾處」（含重複出現），這裡是「多了幾個沒看過的真值」。
    """
    if not counts:
        return ""
    detail = "、".join(f"{t} x{n}" for t, n in sorted(counts.items()))
    total = sum(counts.values())
    return f"本輪新增 {total} 筆個資（{detail}）"


def format_warning(counts: dict[str, int]) -> str:
    """把摘要格式化成一行警告訊息。無偵測結果時回傳空字串。"""
    if not counts:
        return ""
    detail = "、".join(f"{t} x{n}" for t, n in sorted(counts.items()))
    total = sum(counts.values())
    return f"偵測到 {total} 筆敏感資訊（{detail}）"
