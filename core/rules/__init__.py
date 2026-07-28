"""core.rules 套件：統一匯出所有 PII 偵測規則，並提供 detect_all() 一次執行全部規則。"""

from core.rules.api_key import detect_api_key, is_valid_api_key
from core.rules.credit_card import detect_credit_card, is_valid_credit_card
from core.rules.email import detect_email, is_valid_email
from core.rules.tw_id import detect_tw_id, is_valid_tw_id
from core.rules.tw_nhi import detect_tw_nhi, is_valid_tw_nhi
from core.rules.tw_phone_l import detect_tw_phone_l, is_valid_tw_phone_l
from core.rules.tw_phone_m import detect_tw_phone_m, is_valid_tw_phone_m
from core.rules.tw_tax import detect_tw_tax, is_valid_tw_tax

__all__ = [
    "detect_api_key",
    "is_valid_api_key",
    "detect_credit_card",
    "is_valid_credit_card",
    "detect_email",
    "is_valid_email",
    "detect_tw_id",
    "is_valid_tw_id",
    "detect_tw_nhi",
    "is_valid_tw_nhi",
    "detect_tw_phone_l",
    "is_valid_tw_phone_l",
    "detect_tw_phone_m",
    "is_valid_tw_phone_m",
    "detect_tw_tax",
    "is_valid_tw_tax",
    "detect_all",
]

# 依序執行的偵測器清單，新增規則時同步加進來即可被 detect_all() 涵蓋
_DETECTORS = (
    detect_tw_id,
    detect_tw_tax,
    detect_tw_nhi,
    detect_tw_phone_m,
    detect_tw_phone_l,
    detect_email,
    detect_credit_card,
    detect_api_key,
)


def detect_all(text: str) -> dict:
    """依序執行 core/rules 底下所有偵測規則，合併所有 spans 後回傳單一結果。

    不同規則偵測到的片段若重疊，一律全部保留、不去重也不合併，
    僅依 start（相同時再依 end）排序；重疊衝突留給後續 Layer 4
    整合邏輯處理，符合 docs/interface.md 的約定事項。
    """
    spans = []
    for detector in _DETECTORS:
        spans.extend(detector(text)["spans"])

    spans.sort(key=lambda span: (span["start"], span["end"]))

    return {"text": text, "spans": spans}
