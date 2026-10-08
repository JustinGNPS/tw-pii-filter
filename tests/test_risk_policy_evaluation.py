"""風險策略量化工具的計算契約。"""

from tools.evaluate_risk_policies import (
    POLICIES,
    evaluate,
    evaluate_case,
    load_cases,
    render_markdown,
)


def _case(cases, case_id):
    return next(item for item in cases if item["id"] == case_id)


def test_fixture_包含負向對照與高風險案例():
    cases = load_cases()

    assert len(cases) >= 10
    assert _case(cases, "no_sensitive_data")
    assert _case(cases, "mixed_direct_high_risk")


def test_直接識別資訊在四種策略都不會漏出():
    case = _case(load_cases(), "mixed_direct_high_risk")

    for policy in POLICIES:
        result = evaluate_case(case, policy)
        assert result["protected_value_count"] >= 3
        assert result["leaked_value_count"] == 0
        assert result["unknown_placeholder_count"] == 0
        assert result["restored_placeholder_count"] == result["placeholder_count"]


def test_高風險案例依策略逐步降低且_strict_歸零():
    case = _case(load_cases(), "age_gender_company_position")
    results = {policy: evaluate_case(case, policy) for policy in POLICIES}

    scores = [results[policy]["final_risk"] for policy in POLICIES]
    assert scores == sorted(scores, reverse=True)
    assert results["off"]["final_risk"] >= 0.6
    assert results["permissive"]["final_risk"] < 0.6
    assert results["balanced"]["final_risk"] < 0.3
    assert results["strict"]["final_risk"] == 0.0


def test_無風險案例不會被策略誤改成有風險():
    case = _case(load_cases(), "no_sensitive_data")

    for policy in POLICIES:
        result = evaluate_case(case, policy)
        assert result["baseline_risk"] == 0.0
        assert result["final_risk"] == 0.0
        assert result["applied_types"] == []


def test_總表指標與_markdown_可重現():
    report = evaluate(load_cases(), repeat=1)
    markdown = render_markdown(report)

    assert [item["policy"] for item in report["policies"]] == list(POLICIES)
    assert all(item["direct_identifier_leak_rate"] == 0.0 for item in report["policies"])
    assert all(item["placeholder_restore_success_rate"] == 1.0 for item in report["policies"])
    assert "| `balanced` |" in markdown
    assert "年齡與性別泛化為刻意不可逆" in markdown
