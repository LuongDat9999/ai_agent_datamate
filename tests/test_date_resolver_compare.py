import unittest

from ai_agent_datamate.tool.date_resolver import resolve


class DateResolverCompareTests(unittest.TestCase):
    def test_unaccented_explicit_month_with_lien_truoc(self):
        periods = resolve("so voi thang lien truoc, doanh thu thang 3 nam 2026", "2026-06-16")
        self.assertEqual(periods["target"].as_tuple(), ("2026-03-01", "2026-03-31"))
        self.assertEqual(periods["compare"].as_tuple(), ("2026-02-01", "2026-02-28"))

    def test_month_truoc_do(self):
        periods = resolve("So với tháng trước đó, dthu tháng 7/2026 tăng hay giảm mấy phần trăm?", "2026-08-01")
        self.assertEqual(periods["target"].as_tuple(), ("2026-07-01", "2026-07-31"))
        self.assertEqual(periods["compare"].as_tuple(), ("2026-06-01", "2026-06-30"))

    def test_hon_hay_kem(self):
        periods = resolve("Doanh thu tháng 4/2026 hơn hay kém tháng trước bao nhiêu phần trăm?", "2026-08-01")
        self.assertEqual(periods["target"].as_tuple(), ("2026-04-01", "2026-04-30"))
        self.assertEqual(periods["compare"].as_tuple(), ("2026-03-01", "2026-03-31"))

    def test_january_wraps_to_previous_year(self):
        periods = resolve("doanh thu tháng 1/2025 so với tháng trước", "2025-02-01")
        self.assertEqual(periods["compare"].as_tuple(), ("2024-12-01", "2024-12-31"))

    def test_quarter_previous(self):
        periods = resolve("quý 1/2025 so với quý trước", "2026-08-24")
        self.assertEqual(periods["target"].as_tuple(), ("2025-01-01", "2025-03-31"))
        self.assertEqual(periods["compare"].as_tuple(), ("2024-10-01", "2024-12-31"))

    def test_relative_month_target(self):
        periods = resolve("doanh thu tháng này so với tháng trước tăng bao nhiêu", "2026-08-24")
        self.assertEqual(periods["target"].as_tuple(), ("2026-08-01", "2026-08-24"))
        self.assertEqual(periods["compare"].as_tuple(), ("2026-07-01", "2026-07-31"))

    def test_same_period_last_year(self):
        periods = resolve("doanh thu tháng 3/2026 so với năm ngoái", "2026-08-24")
        self.assertEqual(periods["compare"].as_tuple(), ("2025-03-01", "2025-03-31"))

    def test_explicit_comparison_period_wins_over_previous_month(self):
        periods = resolve(
            "Doanh thu tháng 3/2026 so với tháng 3/2025 tăng hay giảm bao nhiêu %?",
            "2026-08-24",
        )
        self.assertEqual(periods["target"].as_tuple(), ("2026-03-01", "2026-03-31"))
        self.assertEqual(periods["compare"].as_tuple(), ("2025-03-01", "2025-03-31"))

    def test_unaccented_same_month_last_year(self):
        periods = resolve(
            "so vs cung thang nam truoc, dthu thang 4 nam 2026 thay doi bao nhieu phan tram",
            "2026-08-24",
        )
        self.assertEqual(periods["compare"].as_tuple(), ("2025-04-01", "2025-04-30"))

    def test_informal_today_and_yesterday(self):
        yesterday = resolve("dthu hqua la bn", "2026-08-24")
        today = resolve("xem giup dthu bua nay la bnhiu", "2026-08-24")
        self.assertEqual(yesterday["yesterday"].as_tuple(), ("2026-08-23", "2026-08-23"))
        self.assertEqual(today["today"].as_tuple(), ("2026-08-24", "2026-08-24"))

    def test_regular_revenue_has_no_compare_keys(self):
        periods = resolve("tổng doanh thu tháng 3/2026", "2026-08-24")
        self.assertNotIn("target", periods)
        self.assertNotIn("compare", periods)


if __name__ == "__main__":
    unittest.main()
