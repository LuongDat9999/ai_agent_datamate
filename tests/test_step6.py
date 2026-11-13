import unittest

import pandas as pd

from ai_agent_datamate.dataprocess.profiler import profile_table
from ai_agent_datamate.llm.generator import _candidate_from_response
from ai_agent_datamate.sqlrunner.validator import semantic_lint
from ai_agent_datamate.tool.entity_matcher import match_entities


class Step6Tests(unittest.TestCase):
    def test_entity_matcher_keeps_meaningful_span_with_stopword(self):
        index = {"values": {"item_name": {"sales": ["Thuốc lá Vinataba", "Bánh khác"]}}}
        hints = match_entities("bán Thuốc lá Vinataba", index)
        self.assertTrue(any(h.value == "Thuốc lá Vinataba" and h.table == "sales" for h in hints))
        self.assertFalse(any(h.value == "lá" for h in hints))

    def test_validator_uses_event_date_role_not_column_name(self):
        frame = pd.DataFrame({"when_happened": ["2026-01-01"], "amount": [1]})
        profile = profile_table("sales", frame)
        profile.roles["when_happened"] = {"role": "event_date", "confidence": 0.9}
        flags = semantic_lint(
            "SELECT SUM(amount) FROM sales WHERE when_happened = '2026-01-01'",
            type("Context", (), {"tables": {"sales": profile}})(),
            "doanh thu tháng 1/2026",
        )
        self.assertNotIn("NO_DATE_FILTER", flags)

    def test_state_specific_question_does_not_require_status_filter(self):
        frame = pd.DataFrame({"state_value": ["cancelled"], "amount": [1]})
        profile = profile_table("sales", frame)
        profile.roles["state_value"] = {"role": "status", "confidence": 0.9}
        flags = semantic_lint(
            "SELECT COUNT(*) FROM sales",
            type("Context", (), {"tables": {"sales": profile}})(),
            "bao nhiêu đơn bị hủy",
        )
        self.assertNotIn("NO_STATUS_FILTER", flags)

    def test_line_count_requires_distinct_order_key(self):
        frame = pd.DataFrame({
            "ma_don": ["A", "A"],
            "trang_thai": ["done", "done"],
            "ngay": ["2026-01-01", "2026-01-01"],
        })
        profile = profile_table("_line_sales", frame)
        profile.grain = "line"
        profile.roles.update({
            "ma_don": {"role": "order_key"},
            "trang_thai": {"role": "status"},
            "ngay": {"role": "event_date"},
        })
        ctx = type("Context", (), {"tables": {"_line_sales": profile}})()
        blocked = semantic_lint(
            "SELECT COUNT(*) FROM _line_sales WHERE ngay = '2026-01-01'",
            ctx,
            "số đơn hôm qua",
        )
        allowed = semantic_lint(
            "SELECT COUNT(DISTINCT ma_don) FROM _line_sales "
            "WHERE trang_thai_chuan = 'hoan_thanh' AND ngay = '2026-01-01'",
            ctx,
            "số đơn hôm qua",
        )
        self.assertIn("LINE_GRAIN_ORDER_METRIC", blocked)
        self.assertNotIn("LINE_GRAIN_ORDER_METRIC", allowed)
        self.assertNotIn("NO_STATUS_FILTER", allowed)
        self.assertNotIn("NO_DATE_FILTER", allowed)

    def test_revenue_without_status_filter_is_blocked(self):
        frame = pd.DataFrame({"trang_thai": ["done"], "ngay": ["2026-01-01"], "tong_tien": [1]})
        profile = profile_table("sales", frame)
        profile.roles.update({
            "trang_thai": {"role": "status"},
            "ngay": {"role": "event_date"},
        })
        flags = semantic_lint(
            "SELECT SUM(tong_tien) FROM sales WHERE ngay = '2026-01-01'",
            type("Context", (), {"tables": {"sales": profile}})(),
            "doanh thu tháng này",
        )
        self.assertIn("NO_STATUS_FILTER", flags)

    def test_truncated_sql_is_marked_parse_error(self):
        candidate = _candidate_from_response("<type>number</type><sql>SELECT SUM(x) FROM</sql>")
        self.assertIn("PARSE_ERROR", candidate.flags)


if __name__ == "__main__":
    unittest.main()
