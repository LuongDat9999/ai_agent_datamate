import unittest

import duckdb

from ai_agent_datamate.llm.generator import Candidate
from ai_agent_datamate.sqlrunner.validator import static_check, validate_candidate


def _ctx(tables=None, con=None):
    attrs = {"tables": tables or {}}
    if con is not None:
        attrs["con"] = con
    return type("Context", (), attrs)()


class ValidatorTests(unittest.TestCase):
    PERIOD_SQL = (
        "WITH first_period AS (SELECT SUM(tong_tien) AS value FROM donhang_chitiet "
        "WHERE ngay BETWEEN DATE '2026-01-01' AND DATE '2026-01-31'), "
        "second_period AS (SELECT SUM(tong_tien) AS value FROM donhang_chitiet "
        "WHERE ngay BETWEEN DATE '2025-12-01' AND DATE '2025-12-31') "
        "SELECT first_period.value - second_period.value AS diff "
        "FROM first_period {join} second_period"
    )

    def test_period_ctes_cross_join_passes(self):
        ctx = _ctx(tables={"donhang_chitiet": {}})
        for join in ("CROSS JOIN", ","):
            with self.subTest(join=join):
                sql = self.PERIOD_SQL.format(join=join)
                errors, flags = static_check(sql, ctx)
                self.assertEqual(errors, [])
                self.assertNotIn("CARTESIAN", flags)

    def test_real_table_join_without_on_is_blocked(self):
        ctx = _ctx(tables={"orders": {}, "customers": {}})
        for sql in ("SELECT * FROM orders, customers",
                    "SELECT * FROM orders CROSS JOIN customers"):
            with self.subTest(sql=sql):
                errors, flags = static_check(sql, ctx)
                self.assertIn("CARTESIAN", flags)
                self.assertTrue(errors)

    def test_mysql_date_sub_is_rewritten_and_runs(self):
        con = duckdb.connect(":memory:")
        ctx = _ctx(con=con)
        candidate = Candidate("SELECT date_sub(DATE '2026-01-10', INTERVAL 3 DAY) AS d", "date")
        validate_candidate(candidate, ctx)
        self.assertIsNone(candidate.error)
        self.assertIsNotNone(candidate.rows)
        self.assertIn("- INTERVAL", candidate.sql)
        self.assertEqual(candidate.rows[0][0].strftime("%Y-%m-%d"), "2026-01-07")

    def test_current_date_is_blocked(self):
        ctx = _ctx()
        for sql in ("SELECT current_date", "SELECT current_timestamp",
                    "SELECT now()", "SELECT today()"):
            with self.subTest(sql=sql):
                errors, flags = static_check(sql, ctx)
                self.assertTrue(errors)
                self.assertTrue(any("periods" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
