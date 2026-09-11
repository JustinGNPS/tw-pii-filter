"""Layer 3 殘餘風險降階規劃與文字泛化。

這個模組刻意把「要移除哪些準識別子」與 proxy 的佔位符機制分開：

- 本模組依策略與目前分數產生可重現的降階計畫。
- COMPANY／POSITION／ADDRESS 等 span 型別由 proxy 以既有 MappingTable 遮蔽，
  因此回程仍能還原。
- AGE／GENDER 沒有 detector span，才在這裡做不可逆但保留語意的文字泛化。

所有策略都是 opt-in；``off`` 完整保留舊行為。
"""

from __future__ import annotations

import re
from typing import Iterable

from core.risk.combination_risk import (
    RISK_SCORE_CAP,
    WARNING_THRESHOLD,
    WEIGHT_BY_TYPE,
    _AGE_CHINESE_PATTERN,
    _AGE_DIGIT_PATTERN,
    _AGE_MINGUO_PATTERN,
    _AGE_WESTERN_YEAR_PATTERN,
    _GENDER_BY_KEYWORD,
    _chinese_number_to_int,
)

POLICY_OFF = "off"
POLICY_PERMISSIVE = "permissive"
POLICY_BALANCED = "balanced"
POLICY_STRICT = "strict"
POLICIES = frozenset(
    {POLICY_OFF, POLICY_PERMISSIVE, POLICY_BALANCED, POLICY_STRICT}
)

# 小於門檻才算達標，與 combination_risk 的高／中／低邊界一致。
POLICY_THRESHOLDS = {
    POLICY_OFF: None,
    POLICY_PERMISSIVE: WARNING_THRESHOLD,
    POLICY_BALANCED: 0.3,
    POLICY_STRICT: 0.0,
}

# 依「資訊用途損失」由低到高排列。性別通常最容易省略；公司名稱遮成可還原
# 佔位符後仍保有句型；精確年齡可保留成人／未成年／高齡等粗粒度；職稱常是
# 任務上下文的核心，因此最後才處理。
REDUCTION_PRIORITY = (
    "GENDER",
    "COMPANY",
    "AGE",
    "ADDRESS",
    "ORGANIZATION",
    "GOVERNMENT",
    "SCENE",
    "POSITION",
)

TEXT_GENERALIZATION_TYPES = frozenset({"AGE", "GENDER"})


def normalize_policy(policy: str | None) -> str:
    """正規化策略名稱；未知值明確失敗，不悄悄套用較弱的保護。"""
    value = (policy or POLICY_OFF).strip().lower()
    if value not in POLICIES:
        allowed = ", ".join(sorted(POLICIES))
        raise ValueError(f"未知的風險降階策略 {policy!r}；可用值：{allowed}")
    return value


def score_types(types: Iterable[str]) -> float:
    """用 Layer 3 同一份權重計算一組型別的理論分數。"""
    unique = set(types)
    if len(unique) < 2:
        return 0.0
    score = sum(WEIGHT_BY_TYPE.get(item, 0.15) for item in unique)
    return round(min(RISK_SCORE_CAP, score), 3)


def plan_reduction(assessment: dict, policy: str | None) -> dict:
    """依目前評分規劃最少的型別移除步驟，不直接修改文字。"""
    normalized = normalize_policy(policy)
    initial_types = set(assessment.get("contributing_types") or [])
    remaining = set(initial_types)
    initial_score = float(assessment.get("score", score_types(remaining)))
    threshold = POLICY_THRESHOLDS[normalized]
    steps: list[dict] = []

    if normalized == POLICY_OFF:
        return {
            "policy": normalized,
            "threshold": threshold,
            "initial_score": initial_score,
            "planned_score": initial_score,
            "applied_types": [],
            "steps": [],
            "met_target": True,
        }

    def needs_more() -> bool:
        if normalized == POLICY_STRICT:
            return bool(remaining)
        return score_types(remaining) >= float(threshold)

    for pii_type in REDUCTION_PRIORITY:
        if not needs_more():
            break
        if pii_type not in remaining:
            continue
        score_before = score_types(remaining)
        remaining.remove(pii_type)
        score_after = score_types(remaining)
        steps.append(
            {
                "type": pii_type,
                "method": (
                    "generalize" if pii_type in TEXT_GENERALIZATION_TYPES else "mask"
                ),
                "score_before": score_before,
                "score_after": score_after,
            }
        )

    planned_score = score_types(remaining)
    met_target = (
        not remaining
        if normalized == POLICY_STRICT
        else planned_score < float(threshold)
    )
    return {
        "policy": normalized,
        "threshold": threshold,
        "initial_score": initial_score,
        "planned_score": planned_score,
        "applied_types": [step["type"] for step in steps],
        "steps": steps,
        "met_target": met_target,
    }


def _age_group(age: int | None) -> str:
    if age is None:
        return "某年齡層"
    if age < 18:
        return "未成年年齡層"
    if age >= 65:
        return "高齡年齡層"
    return "成年年齡層"


def _replace_pattern(text: str, pattern: re.Pattern, replacement) -> tuple[str, int]:
    return pattern.subn(replacement, text)


def generalize_text_types(text: str, selected_types: Iterable[str]) -> tuple[str, list[dict]]:
    """泛化沒有 detector span 的 AGE/GENDER，回傳安全的摘要而非原始值。"""
    selected = set(selected_types)
    actions: list[dict] = []

    if "AGE" in selected:
        count = 0
        text, changed = _replace_pattern(
            text,
            _AGE_DIGIT_PATTERN,
            lambda match: _age_group(int(match.group(1))),
        )
        count += changed
        text, changed = _replace_pattern(
            text,
            _AGE_CHINESE_PATTERN,
            lambda match: _age_group(_chinese_number_to_int(match.group(1))),
        )
        count += changed
        text, changed = _replace_pattern(text, _AGE_MINGUO_PATTERN, "某年齡層")
        count += changed
        text, changed = _replace_pattern(text, _AGE_WESTERN_YEAR_PATTERN, "某年齡層")
        count += changed
        if count:
            actions.append(
                {
                    "type": "AGE",
                    "method": "generalize",
                    "occurrences": count,
                    "replacement": "年齡層",
                }
            )

    if "GENDER" in selected:
        keywords = sorted(_GENDER_BY_KEYWORD, key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(item) for item in keywords))
        text, count = pattern.subn("人士", text)
        if count:
            actions.append(
                {
                    "type": "GENDER",
                    "method": "generalize",
                    "occurrences": count,
                    "replacement": "人士",
                }
            )

    return text, actions
