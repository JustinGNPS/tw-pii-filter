"""還原：把 AI 回覆裡的佔位符換回真值。

分兩種情況：

- **非串流**：整包 JSON 收齊後一次替換，單純的字串取代
- **串流**：回覆一段一段抵達，`[TW_ID_1]` 可能被切成 `[TW` 與 `_ID_1]`
  兩次送達，必須先攢在 buffer 裡等湊齊 —— 見 `StreamRestorer`

查不到的佔位符一律原樣保留（見 `MappingTable.value_for` 的說明）。
"""

import codecs
import json

from proxy.mapping import MAX_TOKEN_LENGTH, TOKEN_PATTERN, MappingTable


def _json_inner(value: str) -> str:
    """把真值轉成可直接放進 JSON 字串內部的形式。

    還原是在「已序列化的 JSON 位元組」上做字串取代，替換進去的真值因此
    必須先做 JSON 逸出，否則含有引號或反斜線的值會破壞整包 JSON。
    目前的個資型別都不含這類字元，但不該賭在這件事上。
    """
    return json.dumps(value, ensure_ascii=False)[1:-1]


def restore_text(text: str, table: MappingTable, for_json: bool = False) -> tuple[str, int, int]:
    """把文字裡所有已知佔位符換回真值。

    回傳 `(還原後的文字, 換回的筆數, 查不到而原樣保留的筆數)`。
    `for_json=True` 時，替換進去的真值會做 JSON 逸出。
    """
    restored = 0
    unknown = 0

    def replace(match: "object") -> str:
        nonlocal restored, unknown
        token = match.group(0)
        value = table.value_for(token)
        if value is None:
            unknown += 1
            return token  # 查不到就原樣保留，絕不猜測
        restored += 1
        return _json_inner(value) if for_json else value

    return TOKEN_PATTERN.sub(replace, text), restored, unknown


def restore_body(body: bytes, table: MappingTable) -> tuple[bytes, int, int]:
    """還原非串流回覆（整包 JSON）。

    解碼失敗時原樣回傳 —— 還原失敗不能讓 agent 拿不到回覆。
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body, 0, 0

    restored_text, restored, unknown = restore_text(text, table, for_json=True)
    return restored_text.encode("utf-8"), restored, unknown


class StreamRestorer:
    """串流還原：處理被切成兩半的佔位符。

    用法：每收到一段文字就 `feed()`，最後 `flush()` 收尾。
    `feed()` 回傳「已確定可以安全送出」的部分，可能比餵進去的短
    —— 尾端疑似半個佔位符的內容會先留在 buffer 裡。

    判斷方式：找出最後一個 `[`。
    - 它後面已經有 `]` → 佔位符是完整的，整段都能送
    - 它後面還沒有 `]` → 從那個 `[` 開始先留著，等下一段
    - 但若已經累積超過 `MAX_TOKEN_LENGTH` 還沒等到 `]`，
      那就不可能是佔位符（例如程式碼裡的陣列語法），直接放行，
      避免無止盡地卡住輸出
    """

    def __init__(self, table: MappingTable) -> None:
        self._table = table
        self._buffer = ""
        self.restored = 0
        self.unknown = 0

    def feed(self, chunk: str) -> str:
        self._buffer += chunk

        start = self._buffer.rfind("[")
        if start == -1 or "]" in self._buffer[start:]:
            safe, self._buffer = self._buffer, ""
        elif len(self._buffer) - start > MAX_TOKEN_LENGTH:
            safe, self._buffer = self._buffer, ""
        else:
            safe, self._buffer = self._buffer[:start], self._buffer[start:]

        return self._restore(safe)

    def flush(self) -> str:
        """把 buffer 裡剩下的內容吐出來（串流結束時呼叫）。"""
        remaining, self._buffer = self._buffer, ""
        return self._restore(remaining)

    def _restore(self, text: str) -> str:
        if not text:
            return text
        restored_text, restored, unknown = restore_text(text, self._table)
        self.restored += restored
        self.unknown += unknown
        return restored_text


class SSERestorer:
    """在 SSE 位元組串流上做還原。

    比 `StreamRestorer` 多處理兩件事：

    1. **佔位符可能跨事件**。AI 是一個 token 一個 token 產生的，
       `[TW_ID_1]` 很可能拆在兩個 SSE 事件的 `delta.content` 裡：

       ```
       data: {"choices":[{"delta":{"content":"[TW"}}]}

       data: {"choices":[{"delta":{"content":"_ID_1]"}}]}
       ```

       直接在原始位元組上取代是接不起來的（中間隔著 JSON 與換行），
       因此必須逐事件解析、取出 `delta.content`、串起來交給
       `StreamRestorer`，再重新序列化送出。

    2. **多位元組字元可能被切在兩個位元組區塊之間**。中文佔 3 個位元組，
       直接對每個 chunk 呼叫 `decode()` 會炸，因此用增量解碼器。

    目前處理 OpenAI chat completions 的串流格式（`choices[].delta.content`）。
    其他協定（如 Anthropic）欄位位置不同，屆時在此擴充即可。
    """

    _EVENT_SEPARATOR = "\n\n"
    _DATA_PREFIX = "data: "
    _DONE = "[DONE]"

    def __init__(self, table: MappingTable) -> None:
        self._text = StreamRestorer(table)
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._pending = ""  # 尚未收完的事件
        self._template: dict | None = None  # 最後一個事件，收尾時當範本用

    @property
    def restored(self) -> int:
        return self._text.restored

    @property
    def unknown(self) -> int:
        return self._text.unknown

    def feed(self, chunk: bytes) -> bytes:
        self._pending += self._decoder.decode(chunk)

        # 只處理已經收完整的事件，最後一段可能還沒收完，留著等下一次
        blocks = self._pending.split(self._EVENT_SEPARATOR)
        self._pending = blocks.pop()

        out = "".join(self._process(block) + self._EVENT_SEPARATOR for block in blocks)
        return out.encode("utf-8")

    def flush(self) -> bytes:
        """串流結束時收尾：吐出殘留的事件與 buffer 裡剩下的文字。"""
        out = ""
        if self._pending:
            out += self._process(self._pending)
            self._pending = ""

        leftover = self._text.flush()
        if leftover:
            out = self._as_event(leftover) + self._EVENT_SEPARATOR + out
        return out.encode("utf-8")

    def _process(self, block: str) -> str:
        """處理一個完整的 SSE 事件區塊。無法解析時原樣回傳。"""
        lines = []
        for line in block.split("\n"):
            if not line.startswith(self._DATA_PREFIX):
                lines.append(line)
                continue

            data = line[len(self._DATA_PREFIX) :]
            if data.strip() == self._DONE:
                # [DONE] 之前必須先把 buffer 裡剩下的文字送出去，
                # 否則結尾的半個佔位符會永遠卡住
                leftover = self._text.flush()
                if leftover:
                    lines.append(self._as_event(leftover))
                    lines.append("")
                lines.append(line)
                continue

            try:
                obj = json.loads(data)
            except (ValueError, TypeError):
                lines.append(line)  # 不是 JSON 就別動它
                continue

            self._template = obj
            self._restore_in_place(obj)
            lines.append(self._DATA_PREFIX + json.dumps(obj, ensure_ascii=False))

        return "\n".join(lines)

    def _restore_in_place(self, obj: dict) -> None:
        """把事件裡每個 `delta.content` 換成還原後（可能較短）的文字。"""
        choices = obj.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                delta["content"] = self._text.feed(delta["content"])

    def _as_event(self, content: str) -> str:
        """把殘留文字包成一個 SSE 事件，沿用最後一個事件的外框。"""
        if self._template is None:
            return ""
        obj = json.loads(json.dumps(self._template))  # 深拷貝，不動到範本
        choices = obj.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choices[0]["delta"] = {"content": content}
            choices[0].pop("finish_reason", None)
            del choices[1:]
        return self._DATA_PREFIX + json.dumps(obj, ensure_ascii=False)
