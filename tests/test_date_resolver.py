import unittest

from ai_agent_datamate.decision.checker import decide
from ai_agent_datamate.llm.generator import Candidate
from ai_agent_datamate.tool.date_resolver import resolve, resolve_time


CASES = [
    ("quý 4/2025", "quarter", "2025-10-01", "2025-12-31"),
    ("quy 4 nam 2025", "quarter", "2025-10-01", "2025-12-31"),
    ("q4/2025", "quarter", "2025-10-01", "2025-12-31"),
    ("tháng 4/2025", "month", "2025-04-01", "2025-04-30"),
    ("thg 4 2025", "month", "2025-04-01", "2025-04-30"),
    ("t4/2025", "month", "2025-04-01", "2025-04-30"),
    ("tháng 04 năm 2025", "month", "2025-04-01", "2025-04-30"),
    ("tháng 8/2026", "month", "2026-08-01", "2026-08-31"),
    ("nam 2025", "year", "2025-01-01", "2025-12-31"),
    ("năm 2025", "year", "2025-01-01", "2025-12-31"),
    ("4/2025", "month", "2025-04-01", "2025-04-30"),
    ("quý 1/2024", "quarter", "2024-01-01", "2024-03-31"),
    ("quy 2 nam 2023", "quarter", "2023-04-01", "2023-06-30"),
    ("q3/2022", "quarter", "2022-07-01", "2022-09-30"),
    ("tháng 1/2021", "month", "2021-01-01", "2021-01-31"),
    ("thg 12 2020", "month", "2020-12-01", "2020-12-31"),
    ("t9/2019", "month", "2019-09-01", "2019-09-30"),
    ("tháng 02 năm 2018", "month", "2018-02-01", "2018-02-28"),
    ("5/2017", "month", "2017-05-01", "2017-05-31"),
    ("quý 4 năm 2016", "quarter", "2016-10-01", "2016-12-31"),
    ("năm 2015", "year", "2015-01-01", "2015-12-31"),
]


class DateResolverTests(unittest.TestCase):
    def test_explicit_period_year_wins(self):
        for question, key, start, end in CASES:
            with self.subTest(question=question):
                periods = resolve(question, "2026-08-24")
                self.assertEqual(periods[key].as_tuple(), (start, end))

    def test_relative_periods_keep_existing_logic(self):
        periods = resolve("tháng trước và quý này", "2026-08-24")
        self.assertEqual(periods["previous"].as_tuple(), ("2026-07-01", "2026-07-31"))
        self.assertEqual(periods["current"].as_tuple(), ("2026-07-01", "2026-08-24"))

    def test_explicit_period_can_be_future_and_is_flagged(self):
        result = resolve_time("tháng 12/2026", "2026-08-24")
        self.assertIn("FUTURE_PERIOD", result["warnings"])

    def test_explicit_quarter_regression_case(self):
        result = resolve_time("e so don hang quy 4/2025 la bao nhiu vay", "2026-08-24")
        self.assertEqual(result["periods"]["quarter"]["start"], "2025-10-01")
        self.assertEqual(result["periods"]["quarter"]["end"], "2025-12-31")

    def test_quarter_spelling_variants(self):
        for question in ("quys 3/2025", "quy 1 nam 2026", "quý 2/2025", "qui 4 nam 2024", "q3/2025"):
            with self.subTest(question=question):
                self.assertIn("quarter", resolve(question, "2026-08-24"))

    def test_explicit_month_anchors_previous_period(self):
        periods = resolve("doanh thu tháng 5/2026 tăng bao nhiêu % so với tháng trước", "2026-08-24")
        self.assertEqual(periods["target"].as_tuple(), ("2026-05-01", "2026-05-31"))
        self.assertEqual(periods["compare"].as_tuple(), ("2026-04-01", "2026-04-30"))
        self.assertNotIn("same_period_last_year", periods)

    def test_explicit_quarter_anchors_previous_quarter_across_year(self):
        periods = resolve("quý 1/2025 so với quý trước", "2026-08-24")
        self.assertEqual(periods["target"].as_tuple(), ("2025-01-01", "2025-03-31"))
        self.assertEqual(periods["compare"].as_tuple(), ("2024-10-01", "2024-12-31"))

    def test_leap_year_same_period_has_half_open_end(self):
        result = resolve_time("tháng 2/2024", "2025-01-01")
        self.assertEqual(result["periods"]["month"]["end_exclusive"], "2024-03-01")

    def test_period_without_date_filter_abstains(self):
        candidate = Candidate(
            "SELECT COUNT(*) FROM ban_hang", "number", rows=[[1]], columns=["count"],
            flags=["NO_DATE_FILTER"],
        )
        decision, winner = decide(
            [candidate],
            capabilities={},
            question="doanh thu tháng 4/2025",
            periods={"month": {"start": "2025-04-01", "end": "2025-04-30"}},
        )
        self.assertEqual(decision, "abstain")
        self.assertIsNone(winner)
        self.assertIn("NO_DATE_FILTER", candidate.flags)


if __name__ == "__main__":
    unittest.main()
