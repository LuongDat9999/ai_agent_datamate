import unittest

import pandas as pd

from ai_agent_datamate.dataprocess.loader import _connect_duckdb
from ai_agent_datamate.dataprocess.profiler import infer_table_semantics, profile_table, render_schema
from ai_agent_datamate.tool.prompts import SYSTEM_PROMPT


class SemanticProfileTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({
            "order_code": ["a", "a", "b", "b", "total"],
            "event_date": pd.to_datetime(["2026-01-01"] * 5),
            "unit_price": [10, 20, 30, 40, 100],
            "quantity": [1, 2, 1, 1, 4],
            "line_amount": [10, 40, 30, 40, 120],
            "is_total": [False, False, False, False, True],
        })

    def test_infers_line_grain_roles_and_measures(self):
        semantics = infer_table_semantics(self.frame)
        self.assertEqual(semantics["grain"], "line")
        self.assertEqual(semantics["roles"]["order_code"]["role"], "order_key")
        self.assertEqual(semantics["roles"]["line_amount"]["role"], "line_amount")
        self.assertEqual(semantics["measures"]["order_count"], "COUNT(DISTINCT order_code)")

    def test_generated_views_preserve_order_count_and_exclude_total(self):
        con = _connect_duckdb({"sales": self.frame})
        line_count = con.execute('SELECT COUNT(*) FROM "_line_sales"').fetchone()[0]
        order_count = con.execute('SELECT COUNT(*) FROM "_order_sales"').fetchone()[0]
        revenue = con.execute('SELECT SUM("line_amount") FROM "_line_sales"').fetchone()[0]
        self.assertEqual(line_count, 4)
        self.assertEqual(order_count, 2)
        self.assertEqual(revenue, 120)

    def test_schema_exposes_roles_and_measure_contract(self):
        profile = profile_table("sales", self.frame)
        semantics = infer_table_semantics(self.frame)
        profile.grain = semantics["grain"]
        profile.grain_confidence = semantics["grain_confidence"]
        profile.roles = semantics["roles"]
        profile.measures = semantics["measures"]
        schema = render_schema({"sales": profile})
        self.assertIn("grain=line", schema)
        self.assertIn("MEASURES", schema)
        self.assertIn("role=order_key", schema)

    def test_prompt_requires_semantic_views_and_grounded_filters(self):
        self.assertIn("_line_*", SYSTEM_PROMPT)
        self.assertIn("_order_*", SYSTEM_PROMPT)
        self.assertIn("gợi ý thực thể hoặc luật nghiệp vụ", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
