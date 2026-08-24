"""core.rules 套件：統一匯出所有 PII 偵測規則，並提供 detect_all() 一次執行全部規則。"""

from core.risk.combination_risk import compute_combination_risk
from core.rules.api_key import detect_api_key, is_valid_api_key
from core.rules.conflict_resolver import renumber_replacements, resolve_overlaps
from core.rules.credit_card import detect_credit_card, is_valid_credit_card
from core.rules.email import detect_email, is_valid_email
from core.rules.normalize import has_full_width, to_half_width
from core.rules.tw_id import detect_tw_id, is_valid_tw_id
from core.rules.tw_nhi import detect_tw_nhi, is_valid_tw_nhi
from core.rules.tw_phone_l import detect_tw_phone_l, is_valid_tw_phone_l
from core.rules.tw_phone_m import detect_tw_phone_m, is_valid_tw_phone_m
from core.rules.tw_tax import detect_tw_tax, is_valid_tw_tax

__all__ = [
    "detect_api_key",
    "is_valid_api_key",
    "resolve_overlaps",
    "renumber_replacements",
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


def detect_all(text: str, extra_spans: list = None) -> dict:
    """依序執行 core/rules 底下所有規則（source="rule"），並可透過 extra_spans
    帶入語意層（如 D 的 NER model，source="model"）已產生的 spans 一併整合。
    所有 spans 合併後經 Layer 4（見 core.rules.conflict_resolver）解析重疊
    衝突，回傳互不重疊的單一結果，符合 docs/interface.md 的約定。

    衝突解析完成後會重新編號 replacement 欄位，確保每個 type 的序號從 1
    開始連續遞增、不因仲裁移除的 span 而跳號。

    extra_spans 中每個 span 必須符合 docs/interface.md 定義的欄位格式
    （start/end/type/text/confidence/source/replacement），source 應為 "model"。

    回傳的 `combination_risk` 為 Layer 3 組合風險評分（見
    `core.risk.combination_risk.compute_combination_risk()` 與
    `docs/layer3_spec.md`），依 Layer 4 仲裁後的 spans 計算；沒有組合風險
    （score 為 0，即準識別子共現數 < 2）時為 `None`，屬選填欄位。
    """
    # 全形英數在中文輸入環境很常見（注音全形模式、從 Word/PDF 複製），
    # 但規則層的正則是 [0-9]/[A-Za-z]，對不到全形（issue #21、#27）。
    # 正規化只做定點映射、字元數不變，因此座標可直接沿用到原文。
    scan_text = to_half_width(text) if has_full_width(text) else text

    spans = []
    for detector in _DETECTORS:
        spans.extend(detector(scan_text)["spans"])

    if extra_spans:
        spans.extend(extra_spans)

    spans = resolve_overlaps(spans)
    spans = renumber_replacements(spans)

    # span 的 text 一律取自**原文**，維持 docs/interface.md 的約定
    # text[start:end] == span["text"]——使用者看到的該是自己打的全形原文。
    if scan_text is not text:
        for span in spans:
            span["text"] = text[span["start"]:span["end"]]

    # 用正規化後的文字算 Layer 3：AGE 正則同樣是 [0-9]，全形年齡才抓得到
    risk = compute_combination_risk(scan_text, spans)
    combination_risk = risk if risk["score"] > 0 else None

    return {"text": text, "spans": spans, "combination_risk": combination_risk}
