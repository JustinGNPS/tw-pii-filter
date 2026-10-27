"""以合成案例量化比較四種殘餘風險降階策略。

這支工具只重用正式流程，不另寫一套風險演算法：

1. ``detect_all`` 整合規則層與 fixture 提供的語意實體。
2. ``mask_with_residual`` 執行 Proxy 的基礎遮蔽。
3. ``reduce_residual_text`` 套用 off/permissive/balanced/strict。
4. ``restore_text`` 驗證所有可逆佔位符是否能換回原值。

AGE/GENDER 的泛化是刻意不可逆，因此「還原成功率」只計算佔位符，不把泛化
文字算成還原失敗。所有輸入均來自版本庫內的合成 fixture，不讀取 ``.env``。
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.redact.mapping import MappingTable, TOKEN_PATTERN, normalize_type
from core.redact.restorer import restore_text
from core.risk import reduction
from core.risk.combination_risk import WARNING_THRESHOLD, compute_combination_risk
from core.rules import detect_all
from proxy.masker import mask_with_residual, reduce_residual_text

DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "risk_policy_cases.json"
DEFAULT_MARKDOWN = ROOT / "docs" / "risk_policy_evaluation.md"
POLICIES = ("off", "permissive", "balanced", "strict")

# Proxy 的正式預設政策：公司與職稱辨識結果保留原文，其餘 span 先遮蔽。
# 評估必須固定這個基準，不能受執行機器環境變數影響。
BASELINE_SKIP_TYPES = frozenset({"COMPANY", "POSITION"})

# 出生年轉年齡會受執行日期影響；固定日期才能讓不同成員得到相同結果。
EVALUATION_DATE = date(2026, 10, 8)


def load_cases(path: Path = DEFAULT_FIXTURE) -> list[dict]:
    """讀取並做最小結構驗證，錯誤案例立即失敗而非產生錯誤報告。"""
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != 1:
        raise ValueError("不支援的評估 fixture schema_version")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("評估 fixture 必須包含非空 cases 陣列")
    ids = [case.get("id") for case in cases]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("每個評估案例都必須有不重複的 id")
    return cases


def _nth_index(text: str, value: str, occurrence: int) -> int:
    start = -1
    cursor = 0
    for _ in range(occurrence):
        start = text.find(value, cursor)
        if start < 0:
            raise ValueError(f"fixture 實體 {value!r} 不在案例文字中")
        cursor = start + len(value)
    return start


def fixture_spans(case: dict) -> list[dict]:
    """把人類易讀的 ``{type, text, occurrence}`` 轉成正式 span。"""
    text = case["text"]
    spans = []
    counters: dict[str, int] = {}
    for entity in case.get("entities", []):
        pii_type = normalize_type(entity["type"])
        value = entity["text"]
        occurrence = int(entity.get("occurrence", 1))
        start = _nth_index(text, value, occurrence)
        counters[pii_type] = counters.get(pii_type, 0) + 1
        spans.append(
            {
                "start": start,
                "end": start + len(value),
                "type": pii_type,
                "text": value,
                "confidence": 1.0,
                "source": "model",
                "replacement": f"[{pii_type}_{counters[pii_type]}]",
            }
        )
    return spans


def _empty_risk() -> dict:
    return {
        "score": 0.0,
        "contributing_types": [],
        "risk_level": "低",
        "suggestions": [],
    }


def evaluate_case(case: dict, policy: str) -> dict:
    """執行單一案例與策略，回傳不含原始文字的量化結果。"""
    text = case["text"]
    detection = detect_all(
        text,
        extra_spans=fixture_spans(case),
        today=EVALUATION_DATE,
    )
    original_risk = detection.get("combination_risk") or _empty_risk()

    table = MappingTable(idle_timeout=None)
    baseline_text, residual = mask_with_residual(
        text,
        detection["spans"],
        table,
        BASELINE_SKIP_TYPES,
    )
    baseline_risk = compute_combination_risk(
        baseline_text,
        residual,
        today=EVALUATION_DATE,
    )
    reduced_text, _, final_risk, plan, _ = reduce_residual_text(
        baseline_text,
        residual,
        table,
        policy,
    )

    protected_values = {
        span["text"]
        for span in detection["spans"]
        if normalize_type(span["type"]) not in BASELINE_SKIP_TYPES
    }
    leaked_values = {value for value in protected_values if value in reduced_text}

    _, restored_count, unknown_count = restore_text(reduced_text, table)
    placeholder_count = len(list(TOKEN_PATTERN.finditer(reduced_text)))

    baseline_types = set(baseline_risk.get("contributing_types") or [])
    applied_types = set(plan.get("applied_types") or [])
    exact_retention = (
        len(baseline_types - applied_types) / len(baseline_types)
        if baseline_types
        else None
    )

    return {
        "case_id": case["id"],
        "description": case.get("description", ""),
        "policy": policy,
        "original_risk": original_risk["score"],
        "baseline_risk": baseline_risk["score"],
        "final_risk": final_risk["score"],
        "baseline_types": sorted(baseline_types),
        "final_types": sorted(final_risk.get("contributing_types") or []),
        "applied_types": plan.get("applied_types") or [],
        "met_target": bool(plan.get("met_target")),
        "protected_value_count": len(protected_values),
        "leaked_value_count": len(leaked_values),
        "placeholder_count": placeholder_count,
        "restored_placeholder_count": restored_count,
        "unknown_placeholder_count": unknown_count,
        "exact_quasi_retention_rate": exact_retention,
    }


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(statistics.fmean(items), 4) if items else None


def _percentile(values: list[float], percentile: float) -> float:
    """線性內插百分位數；不依賴額外統計套件。"""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def evaluate_policy(cases: list[dict], policy: str, repeat: int) -> dict:
    if repeat < 1:
        raise ValueError("repeat 必須至少為 1")

    results = []
    latencies = []
    for case in cases:
        first_result = None
        for _ in range(repeat):
            started = time.perf_counter_ns()
            current = evaluate_case(case, policy)
            latencies.append((time.perf_counter_ns() - started) / 1_000_000)
            if first_result is None:
                first_result = current
        results.append(first_result)

    high_before = sum(
        item["baseline_risk"] >= WARNING_THRESHOLD for item in results
    )
    high_after = sum(item["final_risk"] >= WARNING_THRESHOLD for item in results)
    protected_total = sum(item["protected_value_count"] for item in results)
    leaked_total = sum(item["leaked_value_count"] for item in results)
    placeholder_total = sum(item["placeholder_count"] for item in results)
    restored_total = sum(item["restored_placeholder_count"] for item in results)
    retention_values = [
        item["exact_quasi_retention_rate"]
        for item in results
        if item["exact_quasi_retention_rate"] is not None
    ]

    return {
        "policy": policy,
        "case_count": len(results),
        "high_risk_before_count": high_before,
        "high_risk_after_count": high_after,
        "high_risk_reduction_rate": (
            round((high_before - high_after) / high_before, 4)
            if high_before
            else None
        ),
        "mean_baseline_risk": _mean(item["baseline_risk"] for item in results),
        "mean_final_risk": _mean(item["final_risk"] for item in results),
        "direct_identifier_leak_rate": _rate(leaked_total, protected_total),
        "exact_quasi_retention_rate": _mean(retention_values),
        "placeholder_restore_success_rate": _rate(restored_total, placeholder_total),
        "target_met_rate": _rate(
            sum(item["met_target"] for item in results), len(results)
        ),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 4),
            "p50": round(_percentile(latencies, 0.50), 4),
            "p95": round(_percentile(latencies, 0.95), 4),
            "samples": len(latencies),
        },
        "cases": results,
    }


def evaluate(cases: list[dict], repeat: int = 50) -> dict:
    policies = [evaluate_policy(cases, policy, repeat) for policy in POLICIES]
    return {
        "schema_version": 1,
        "evaluation_date": EVALUATION_DATE.isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "repeat_per_case": repeat,
        "baseline_skip_types": sorted(BASELINE_SKIP_TYPES),
        "policies": policies,
    }


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render_markdown(report: dict) -> str:
    """把機器可讀結果轉成可直接放進專題報告的摘要。"""
    by_policy = {item["policy"]: item for item in report["policies"]}
    lines = [
        "# 風險降階策略量化評估",
        "",
        f"> 評估日期：{report['evaluation_date']}　｜　合成案例：{report['policies'][0]['case_count']} 筆"
        f"　｜　每案例重複：{report['repeat_per_case']} 次",
        "",
        "## 評估方法",
        "",
        "本評估使用版本庫內的合成資料，比較 `off`、`permissive`、`balanced`、"
        "`strict` 四種策略。流程與正式 Proxy 相同：先偵測並執行基礎遮蔽，再對"
        "雲端仍可見的準識別資訊套用策略，最後驗證佔位符能否還原。",
        "",
        "- 高風險門檻：殘餘分數大於或等於 0.60。",
        "- 直接識別資訊漏出率：應遮蔽的原值仍出現在送出文字中的比例。",
        "- 精確準識別資訊保留率：策略處理後，仍保持原始精度的準識別型別比例。",
        "- 佔位符還原成功率：送出文字中的可逆佔位符能由記憶體對照表還原的比例。",
        "- 年齡與性別泛化為刻意不可逆，不列入佔位符還原失敗。",
        "",
        "## 總體結果",
        "",
        "| 策略 | 高風險案例（前→後） | 高風險降低率 | 平均殘餘風險 | 精確資訊保留率 | 直接識別資訊漏出率 | 佔位符還原成功率 | P95 延遲 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for policy in POLICIES:
        item = by_policy[policy]
        lines.append(
            f"| `{policy}` | {item['high_risk_before_count']}→{item['high_risk_after_count']} "
            f"| {_percent(item['high_risk_reduction_rate'])} "
            f"| {item['mean_final_risk']:.3f} "
            f"| {_percent(item['exact_quasi_retention_rate'])} "
            f"| {_percent(item['direct_identifier_leak_rate'])} "
            f"| {_percent(item['placeholder_restore_success_rate'])} "
            f"| {item['latency_ms']['p95']:.3f} ms |"
        )

    lines.extend(
        [
            "",
            "## 各案例殘餘風險",
            "",
            "| 案例 | 基礎遮蔽後 | off | permissive | balanced | strict |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    first_cases = by_policy["off"]["cases"]
    case_by_policy = {
        policy: {item["case_id"]: item for item in by_policy[policy]["cases"]}
        for policy in POLICIES
    }
    for case in first_cases:
        case_id = case["case_id"]
        values = [case_by_policy[policy][case_id]["final_risk"] for policy in POLICIES]
        lines.append(
            f"| `{case_id}` | {case['baseline_risk']:.2f} | "
            + " | ".join(f"{value:.2f}" for value in values)
            + " |"
        )

    lines.extend(
        [
            "",
            "## 解讀與限制",
            "",
            "- `permissive` 只處理達到高風險門檻的案例，適合優先保留上下文。",
            "- `balanced` 會把殘餘風險降到 0.30 以下，在風險與資訊保留之間取捨。",
            "- `strict` 移除所有已辨識準識別子，風險最低，但精確資訊保留率也最低。",
            "- 這是固定合成案例的功能性評估，不等同真實世界的匿名保證，也不能取代"
            "不同領域語料上的誤判、漏判與使用者研究。",
            "- 延遲數字只代表本次執行環境，跨裝置比較前應在同一硬體重跑。",
            "",
            "## 可重現指令",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe tools\\evaluate_risk_policies.py --repeat 50",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--repeat", type=int, default=50)
    parser.add_argument("--markdown-out", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate(load_cases(args.fixture), repeat=args.repeat)

    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.write_text(render_markdown(report), encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(f"已評估 {report['policies'][0]['case_count']} 個合成案例。")
    for item in report["policies"]:
        print(
            f"{item['policy']:>10}: 平均風險 {item['mean_final_risk']:.3f}, "
            f"高風險 {item['high_risk_after_count']} 筆, "
            f"P95 {item['latency_ms']['p95']:.3f} ms"
        )
    print(f"Markdown：{args.markdown_out}")


if __name__ == "__main__":
    main()
