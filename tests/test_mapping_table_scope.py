"""驗證「對照表該不該跨請求共用」——回答 C 在 PR #4 提的問題。

## 背景

C 的擴充是「單一對話、12 小時過期」，proxy 目前是「整個行程共用一張表、
永不清除」。B 曾經提過一個未驗證的假設：agent 拿到的回覆已經被還原成真值，
所以下次重送的歷史裡帶的是真值不是佔位符，對照表或許只需要活過
「這次遮蔽 → 這次還原」，不需要跨請求記憶 —— 若成立，改成「一個請求一張表」
會比 C 的 12 小時更嚴格（記憶體不留長期明文、有界、跨請求無法關聯）。

這份測試直接構造一個會讓兩種做法分岔的情境，用結果說話。

## 情境設計

模擬兩輪對話：
- 第 1 輪：文字裡只有客戶 A 的身分證字號
- 第 2 輪：agent 照慣例把整段歷史重新送一次，這次多了一個**新**客戶 B，
  且 B 在文字裡出現的位置**在 A 之前**（這是會發生的真實情況——例如
  新的對話內容被加在檔案或訊息的前面，或者只是剛好那筆資料在陣列裡排前面）

## 結論寫在測試的 assert 裡，跑起來就知道
"""

from core.rules.tw_id import _LETTER_MAP, _WEIGHTS
from core.redact.mapping import MappingTable
from proxy.masker import mask_text
from core.rules import detect_all


def _valid_tw_id(letter: str, first_8_digits: str) -> str:
    n1, n2 = divmod(_LETTER_MAP[letter], 10)
    values = [n1, n2] + [int(d) for d in first_8_digits]
    partial_total = sum(v * w for v, w in zip(values, _WEIGHTS[:10]))
    check_digit = (10 - (partial_total % 10)) % 10
    return f"{letter}{first_8_digits}{check_digit}"


ID_A = _valid_tw_id("A", "00000001")
ID_B = _valid_tw_id("B", "00000001")

TURN_1_TEXT = f"客戶 A 的身分證是 {ID_A}"
# 第 2 輪：新客戶 B 排在文字前面，A（舊客戶）排在後面
TURN_2_TEXT = f"客戶 B 的身分證是 {ID_B}，客戶 A 的身分證是 {ID_A}（先前提過）"


def _mask(text: str, table: MappingTable) -> str:
    spans = detect_all(text)["spans"]
    return mask_text(text, spans, table)


def test_共用一張表_舊客戶跨輪次拿到的佔位符不會變():
    """現行設計（整個行程共用一張表）：客戶 A 在第 2 輪即使排在新客戶 B
    後面出現，也必須拿到跟第 1 輪一模一樣的佔位符 —— 這是 LLM 能認出
    『這兩輪講的是同一個人』的前提，也是 `mapping.py` 文件開頭解釋過的、
    不用 `detect_all()` 自帶 `replacement` 序號的原因。
    """
    table = MappingTable()

    masked_turn_1 = _mask(TURN_1_TEXT, table)
    assert masked_turn_1 == "客戶 A 的身分證是 [TW_ID_1]"

    masked_turn_2 = _mask(TURN_2_TEXT, table)

    # 關鍵斷言：A 在第 2 輪依然是 [TW_ID_1]，跟第 1 輪一致
    assert "[TW_ID_1]" in masked_turn_2
    assert masked_turn_2 == "客戶 B 的身分證是 [TW_ID_2]，客戶 A 的身分證是 [TW_ID_1]（先前提過）"


def test_一個請求一張表_舊客戶跨輪次拿到的佔位符會變_這是問題所在():
    """驗證『一個請求一張表』這個假設**不成立**：如果每輪都用全新的
    MappingTable，客戶 A 在第 2 輪會因為新客戶 B 排在他前面，被搶走
    [TW_ID_1]，改拿到 [TW_ID_2] —— 跟第 1 輪的 [TW_ID_1] 不一致。

    這正是 `mapping.py` 文件開頭警告過的場景（原本是描述『為什麼不能直接用
    detect_all() 的 replacement 序號』），换成『每輪重開一張表』會重新踩到
    一模一樣的坑，只是原因從『A 重新編號』換成『B 的表是空的』。

    後果不是資料外洩（兩種做法下真值都沒有離開本機），而是：
    1. 從 LLM 的角度看，同一個對話裡『這個人』的代號變了，可能造成推理混亂
    2. 如果 agent 的某些操作依賴「跟前一輪回覆的內容做比對」（Aider 的
       diff edit format 就是活生生的例子——這正是第一版遮蔽不還原時
       實際壞掉的原因），編號不一致會是同一類風險的變形
    """
    table_turn_1 = MappingTable()  # 每輪一張全新的表
    table_turn_2 = MappingTable()

    masked_turn_1 = _mask(TURN_1_TEXT, table_turn_1)
    assert masked_turn_1 == "客戶 A 的身分證是 [TW_ID_1]"

    masked_turn_2 = _mask(TURN_2_TEXT, table_turn_2)

    # B 排在文字前面，新表從 1 開始配號，所以 B 才是 [TW_ID_1]
    assert masked_turn_2 == "客戶 B 的身分證是 [TW_ID_1]，客戶 A 的身分證是 [TW_ID_2]（先前提過）"

    # 這就是問題：A 在第 1 輪是 [TW_ID_1]，第 2 輪變成 [TW_ID_2] —— 不一致
    a_placeholder_turn_1 = "[TW_ID_1]"
    a_placeholder_turn_2 = "[TW_ID_2]"
    assert a_placeholder_turn_1 != a_placeholder_turn_2, (
        "如果這個 assert 失敗，代表巧合下沒有分岔——不代表『一個請求一張表』"
        "安全，只代表這組測資沒踩到分岔情境，換一組資料還是會出事"
    )
