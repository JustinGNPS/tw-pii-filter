"""執行中可調整的設定（示範頁「設定」分頁）。

## 為什麼需要

原本所有設定都只能寫在 `.env`、改完要重開 proxy。使用者的需求是「預設全開，
但可以在介面上自己選要過濾什麼」。這裡把可以安全地在執行中切換的設定集中起來，
改了**立即生效**（`proxy/` 底下所有地方都是在呼叫當下才讀 `config.*`，
偵測快取的 key 也已經包含語意層開關，見 `proxy/detector.detect()`）。

## 存在哪

存到 repo 根目錄的 `.pii_settings.json`（不進 git），下次啟動時套用，**優先於 `.env`**。
檔案裡只有型別代碼與策略名稱，不含任何個資。

**語意層開關刻意不存**：啟動器每次啟動都會問要不要開語意層，
如果這裡也存一份，兩邊會互相覆蓋，使用者搞不清楚到底聽誰的。
介面上切換語意層只對這次執行有效。

## 安全

這些設定可以把遮蔽關掉。端點的防護在 `proxy/demo.py`（只接受本機來源的 JSON 請求），
這裡只負責驗證值是否合法。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import threading
from pathlib import Path
from typing import Any

from core.risk import reduction
from proxy import config

logger = logging.getLogger("proxy")

SETTINGS_FILE = Path(__file__).resolve().parent.parent / ".pii_settings.json"

# 介面上列出的型別（順序即顯示順序）與中文名稱
RULE_TYPES = {
    "TW_ID": "身分證字號",
    "TW_TAX": "統一編號",
    "TW_NHI": "健保卡號",
    "TW_PHONE_M": "手機號碼",
    "TW_PHONE_L": "市內電話",
    "EMAIL": "電子郵件",
    "CREDIT_CARD": "信用卡號",
    "API_KEY": "API 金鑰",
}
SEMANTIC_TYPES = {
    "NAME": "姓名",
    "ADDRESS": "地址",
    "POSITION": "職稱",
    "COMPANY": "公司名稱",
}
ALL_TYPES = frozenset(RULE_TYPES) | frozenset(SEMANTIC_TYPES)

# 會存進檔案的鍵（ner_enabled 刻意不在這裡，理由見模組說明）
_PERSISTED = ("mask_types", "risk_warning", "risk_reduction_policy", "image_policy", "document_policy")

_ner_loading = False
_saved_loaded = False
_lock = threading.Lock()


def ner_available() -> bool:
    try:
        return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers"))
    except ValueError:
        # 模組已在 sys.modules 但沒有 __spec__（例如被替換成假模組）時，
        # find_spec 會丟 ValueError 而不是回 None。已經載入就代表可用。
        return True


def current() -> dict[str, Any]:
    return {
        "mask_types": sorted(ALL_TYPES - config.SKIP_TYPES),
        "ner_enabled": config.ENABLE_NER,
        "ner_available": ner_available(),
        "ner_loading": _ner_loading,
        "risk_warning": config.ENABLE_RISK_WARNING,
        "risk_reduction_policy": config.RISK_REDUCTION_POLICY,
        "image_policy": config.IMAGE_POLICY,
        "document_policy": config.DOCUMENT_POLICY,
        "saved": SETTINGS_FILE.exists(),
        "types": {"rule": RULE_TYPES, "semantic": SEMANTIC_TYPES},
        "options": {
            "risk_reduction_policy": sorted(reduction.POLICIES),
            "image_policy": list(config.IMAGE_POLICIES),
            "document_policy": list(config.DOCUMENT_POLICIES),
        },
    }


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    """檢查每個值，全部合法才回傳；任何一個不合法就丟 ValueError，一個都不套用。"""
    if not isinstance(data, dict):
        raise ValueError("設定必須是 JSON 物件")
    clean: dict[str, Any] = {}
    for key, value in data.items():
        if key == "mask_types":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError("mask_types 必須是字串陣列")
            unknown = set(value) - ALL_TYPES
            if unknown:
                raise ValueError(f"不認得的型別：{', '.join(sorted(unknown))}")
            clean[key] = set(value)
        elif key in ("ner_enabled", "risk_warning"):
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必須是 true／false")
            if key == "ner_enabled" and value and not ner_available():
                raise ValueError("語意層套件尚未安裝（pip install -r core/ner/requirements.txt）")
            clean[key] = value
        elif key == "risk_reduction_policy":
            if value not in reduction.POLICIES:
                raise ValueError(f"risk_reduction_policy 可用值：{', '.join(sorted(reduction.POLICIES))}")
            clean[key] = value
        elif key == "image_policy":
            if value not in config.IMAGE_POLICIES:
                raise ValueError(f"image_policy 可用值：{', '.join(config.IMAGE_POLICIES)}")
            clean[key] = value
        elif key == "document_policy":
            if value not in config.DOCUMENT_POLICIES:
                raise ValueError(f"document_policy 可用值：{', '.join(config.DOCUMENT_POLICIES)}")
            clean[key] = value
        else:
            raise ValueError(f"不認得的設定：{key}")
    return clean


def _enable_ner_in_background() -> None:
    """先載入模型，成功才把開關打開。

    第一次載入約 10 秒；這段期間開關仍是關的，請求照常只跑規則層，
    不會有請求卡住等模型。載入失敗就維持關閉並記錄原因。
    """
    global _ner_loading
    try:
        from core.ner.detector import detect_ner

        detect_ner("暖機")
        config.ENABLE_NER = True
        logger.info("語意層已開啟（由設定介面）")
    except Exception as exc:  # noqa: BLE001
        logger.error("語意層載入失敗，維持關閉：%s", exc)
    finally:
        _ner_loading = False


def _apply_clean(clean: dict[str, Any]) -> None:
    global _ner_loading
    if "mask_types" in clean:
        # 不在介面清單裡的型別（例如使用者在 .env 自訂的）維持原本的跳過設定
        others = config.SKIP_TYPES - ALL_TYPES
        config.SKIP_TYPES = frozenset(others | (ALL_TYPES - clean["mask_types"]))
    if "risk_warning" in clean:
        config.ENABLE_RISK_WARNING = clean["risk_warning"]
    if "risk_reduction_policy" in clean:
        config.RISK_REDUCTION_POLICY = clean["risk_reduction_policy"]
    if "image_policy" in clean:
        config.IMAGE_POLICY = clean["image_policy"]
    if "document_policy" in clean:
        config.DOCUMENT_POLICY = clean["document_policy"]
    if "ner_enabled" in clean and clean["ner_enabled"] != config.ENABLE_NER:
        if clean["ner_enabled"]:
            if not _ner_loading:
                _ner_loading = True
                threading.Thread(target=_enable_ner_in_background, daemon=True).start()
        else:
            config.ENABLE_NER = False


def apply(data: dict[str, Any], persist: bool = True) -> dict[str, Any]:
    """驗證並套用設定，回傳套用後的狀態。不合法時丟 ValueError，什麼都不改。"""
    with _lock:
        clean = _validate(data)
        _apply_clean(clean)
        if persist:
            save()
        changed = "、".join(sorted(clean)) or "（無）"
        logger.info("設定已由介面變更：%s", changed)
        return current()


def save() -> None:
    state = current()
    payload = {key: state[key] for key in _PERSISTED}
    SETTINGS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def reset() -> None:
    """刪掉存檔。下次啟動回到 .env／預設值；這次執行中的值不變。"""
    SETTINGS_FILE.unlink(missing_ok=True)


def load_saved() -> bool:
    """啟動時套用存檔。存檔壞掉就忽略並警告，不讓 proxy 起不來。"""
    global _saved_loaded
    if not SETTINGS_FILE.exists():
        return False
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        data = {k: v for k, v in data.items() if k in _PERSISTED}
        _apply_clean(_validate(data))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("設定檔 %s 無法套用，改用 .env／預設值：%s", SETTINGS_FILE.name, exc)
        return False
    _saved_loaded = True
    return True
