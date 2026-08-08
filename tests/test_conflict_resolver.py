"""core.rules.conflict_resolver（Layer 4 重疊衝突解析）的 unit test。"""

import unittest

from core.rules.conflict_resolver import renumber_replacements, resolve_overlaps


def _span(start, end, type_="X", confidence=0.9, source="rule", text=None):
    return {
        "start": start,
        "end": end,
        "type": type_,
        "text": text if text is not None else f"span[{start}:{end}]",
        "confidence": confidence,
        "source": source,
        "replacement": f"[{type_}_1]",
    }


class NoOverlapTests(unittest.TestCase):
    """情況四：不重疊，兩個 span 都應保留。"""

    def test_disjoint_spans_are_both_kept(self):
        a = _span(0, 5, type_="A")
        b = _span(10, 15, type_="B")

        result = resolve_overlaps([a, b])

        self.assertEqual(len(result), 2)
        self.assertIn(a, result)
        self.assertIn(b, result)
        self.assertEqual([r["start"] for r in result], [0, 10])

    def test_adjacent_spans_do_not_overlap(self):
        # end 是不含端點（Python slice 慣例），緊鄰但不重疊的 span 應視為不衝突
        a = _span(0, 5, type_="A")
        b = _span(5, 10, type_="B")

        result = resolve_overlaps([a, b])

        self.assertEqual(len(result), 2)


class ExactOverlapTests(unittest.TestCase):
    """情況一：完全重疊（start/end 完全相同）。"""

    def test_higher_confidence_wins_when_range_identical(self):
        low = _span(0, 10, type_="LOW_CONF", confidence=0.6)
        high = _span(0, 10, type_="HIGH_CONF", confidence=0.95)

        result = resolve_overlaps([low, high])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "HIGH_CONF")

    def test_rule_source_wins_over_model_when_range_and_confidence_identical(self):
        model_span = _span(0, 10, type_="FROM_MODEL", confidence=0.9, source="model")
        rule_span = _span(0, 10, type_="FROM_RULE", confidence=0.9, source="rule")

        result = resolve_overlaps([model_span, rule_span])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "FROM_RULE")
        self.assertEqual(result[0]["source"], "rule")


class PartialOverlapTests(unittest.TestCase):
    """情況二：部分重疊（區間交錯但非包含關係）。"""

    def test_larger_range_wins_on_partial_overlap(self):
        # A: [0, 12) 長度 12；B: [5, 15) 長度 10，兩者交錯重疊於 [5, 12)
        larger = _span(0, 12, type_="LARGER")
        smaller = _span(5, 15, type_="SMALLER")

        result = resolve_overlaps([smaller, larger])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "LARGER")


class ContainmentOverlapTests(unittest.TestCase):
    """情況三：包含關係（一個 span 完全落在另一個範圍內）。"""

    def test_outer_span_wins_over_inner_span(self):
        outer = _span(0, 20, type_="OUTER")
        inner = _span(5, 10, type_="INNER")

        result = resolve_overlaps([inner, outer])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "OUTER")


class ResultInvariantTests(unittest.TestCase):
    """解析後的結果必須互不重疊，且依 start 升序排列。"""

    def test_result_has_no_overlaps_and_is_sorted(self):
        spans = [
            _span(0, 5, type_="A"),
            _span(3, 8, type_="B"),          # 與 A 部分重疊，範圍較小應被移除
            _span(20, 25, type_="C"),
            _span(20, 30, type_="D"),        # 與 C 完全重疊，範圍較大應保留
            _span(40, 45, type_="E"),        # 完全獨立
        ]

        result = resolve_overlaps(spans)

        starts = [span["start"] for span in result]
        self.assertEqual(starts, sorted(starts))

        for i in range(len(result) - 1):
            self.assertLessEqual(result[i]["end"], result[i + 1]["start"])

        types_kept = {span["type"] for span in result}
        self.assertEqual(types_kept, {"A", "D", "E"})


class RenumberReplacementsTests(unittest.TestCase):
    """resolve_overlaps 之後的重新編號，確保不因移除的 span 而跳號。"""

    def test_renumbers_sequentially_per_type_in_start_order(self):
        spans = [
            _span(0, 5, type_="A", text="a1"),
            _span(10, 15, type_="B", text="b1"),
            _span(20, 25, type_="A", text="a2"),
            _span(30, 35, type_="B", text="b2"),
        ]

        result = renumber_replacements(spans)

        self.assertEqual(
            [span["replacement"] for span in result],
            ["[A_1]", "[B_1]", "[A_2]", "[B_2]"],
        )

    def test_closes_gap_left_by_a_span_removed_during_conflict_resolution(self):
        # 兩個同 type 的 span，其中較早出現的那個會在衝突解析時輸給
        # 另一個較大範圍的 span 而被移除；剩下的那個原本編號是第 2 筆，
        # 重新編號後應該變成第 1 筆，不留下缺口。
        loser = _span(0, 5, type_="TW_ID", text="loser")
        winner = _span(0, 20, type_="EMAIL", text="winner")
        survivor = _span(30, 35, type_="TW_ID", text="survivor")
        survivor["replacement"] = "[TW_ID_2]"  # 原始（未經仲裁）編號

        resolved = resolve_overlaps([loser, winner, survivor])
        result = renumber_replacements(resolved)

        tw_id_spans = [span for span in result if span["type"] == "TW_ID"]
        self.assertEqual(len(tw_id_spans), 1)
        self.assertEqual(tw_id_spans[0]["text"], "survivor")
        self.assertEqual(tw_id_spans[0]["replacement"], "[TW_ID_1]")


if __name__ == "__main__":
    unittest.main()
