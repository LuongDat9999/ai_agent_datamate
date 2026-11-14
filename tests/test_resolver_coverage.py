import unittest

from ai_agent_datamate.formatting.format_enforcer import to_answer
from ai_agent_datamate.main import _resolver_candidate


class _Result:
    description = [("value",)]

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, has_rows=True):
        self.has_rows = has_rows

    def execute(self, query):
        if query.startswith("SELECT 1"):
            return _Result([(1,)] if self.has_rows else [])
        return _Result([(42,)])


class _Context:
    coverage_safe = True

    def __init__(self, has_rows=True):
        self.con = _Connection(has_rows)


CFG = {
    "ok": True,
    "table": "sales",
    "date_column": "ngay",
    "order_column": "ma_don",
    "revenue_column": "thanh_tien",
    "grain": "order",
    "status_column": "trang_thai_chuan",
    "status_condition": "TRUE",
    "min_date": "2025-02-01",
    "max_date": "2025-12-31",
}


class ResolverCoverageTests(unittest.TestCase):
    def test_period_before_data_is_unanswerable_with_string_reason(self):
        candidate, reason = _resolver_candidate(
            _Context(), CFG, "revenue_period", "doanh thu tháng 1/2025",
            {"target": {"start": "2025-01-01", "end": "2025-01-31"}}, set(),
        )
        self.assertEqual(reason, "period_outside_data")
        self.assertEqual(candidate.answer_type, "unanswerable")
        record = to_answer("q", "unanswerable", "unanswerable", reason=candidate.reason)
        self.assertEqual(record["type"], "unanswerable")
        self.assertIsInstance(record["reason"], str)

    def test_period_after_data_is_unanswerable(self):
        candidate, reason = _resolver_candidate(
            _Context(), CFG, "revenue_period", "doanh thu tháng 1/2026",
            {"target": {"start": "2026-01-01", "end": "2026-01-31"}}, set(),
        )
        self.assertEqual(reason, "period_outside_data")
        self.assertEqual(candidate.answer_type, "unanswerable")

    def test_compare_period_outside_data_is_unanswerable(self):
        candidate, reason = _resolver_candidate(
            _Context(), CFG, "period_compare", "doanh thu thay đổi bao nhiêu % so với kỳ trước",
            {
                "target": {"start": "2025-03-01", "end": "2025-03-31"},
                "compare": {"start": "2024-12-01", "end": "2024-12-31"},
            }, set(),
        )
        self.assertEqual(reason, "period_outside_data")
        self.assertEqual(candidate.answer_type, "unanswerable")

    def test_compare_without_percent_is_rejected(self):
        candidate, reason = _resolver_candidate(
            _Context(), CFG, "period_compare", "doanh thu chênh lệch so với kỳ trước",
            {
                "target": {"start": "2025-03-01", "end": "2025-03-31"},
                "compare": {"start": "2025-02-01", "end": "2025-02-28"},
            }, set(),
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "compare_not_percent")

    def test_compare_candidate_declares_percent(self):
        candidate, reason = _resolver_candidate(
            _Context(), CFG, "period_compare",
            "doanh thu thay đổi bao nhiêu % so với kỳ trước",
            {
                "target": {"start": "2025-03-01", "end": "2025-03-31"},
                "compare": {"start": "2025-02-01", "end": "2025-02-28"},
            }, set(),
        )
        self.assertIsNone(reason)
        self.assertEqual(candidate.declared_type, "percent")

    def test_invoice_total_revenue_is_rejected(self):
        config = {**CFG, "revenue_basis": "invoice_total"}
        candidate, reason = _resolver_candidate(
            _Context(), config, "revenue_period", "doanh thu tháng 3/2025",
            {"target": {"start": "2025-03-01", "end": "2025-03-31"}}, set(),
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "revenue_basis_invoice_total")

    def test_partial_period_returns_value(self):
        candidate, reason = _resolver_candidate(
            _Context(), CFG, "revenue_period", "doanh thu",
            {"target": {"start": "2025-01-15", "end": "2025-02-15"}}, set(),
        )
        self.assertIsNone(reason)
        self.assertEqual(candidate.rows, [[42]])
        self.assertEqual(candidate.declared_type, "number")

    def test_gap_inside_range_abstains(self):
        candidate, reason = _resolver_candidate(
            _Context(False), CFG, "revenue_period", "doanh thu",
            {"target": {"start": "2025-06-01", "end": "2025-06-30"}}, set(),
        )
        self.assertIsNone(candidate)
        self.assertEqual(reason, "gap_inside_range")


if __name__ == "__main__":
    unittest.main()
