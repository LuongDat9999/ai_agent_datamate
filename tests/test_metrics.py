import unittest

import pandas as pd

from ai_agent_datamate.dataprocess.loader import _connect_duckdb
from ai_agent_datamate.dataprocess.order_status import standardize_statuses
from ai_agent_datamate.tool.metrics import build_metric_sql, infer_metric_config


class MetricTests(unittest.TestCase):
    def test_revenue_prefers_line_amount_over_invoice_total(self):
        tables = {
            "orders": pd.DataFrame({
                "ma_don": ["A", "A", "B"],
                "ngay": ["2025-01-01"] * 3,
                "status": ["Xong"] * 3,
                "so_luong": [2, 1, 1],
                "don_gia": [10, 20, 30],
                "chiet_khau": [2, 0, 0],
                "thanh_tien": [18, 20, 30],
                "tong_sau_giam_gia": [38, 38, 30],
            })
        }
        detected, standardized = standardize_statuses(tables)
        cfg = infer_metric_config(_connect_duckdb(standardized, detected))
        self.assertTrue(cfg["ok"], cfg)
        self.assertEqual(cfg["revenue_column"], "thanh_tien")
        self.assertIn("line", cfg["revenue_column_reason"])

    def test_status_filter_keeps_shipping_and_unknown_and_excludes_orders(self):
        tables = {
            "orders": pd.DataFrame({
                "ma_don": ["A", "A", "B", "C", "D"],
                "ngay": ["2025-01-01"] * 5,
                "status": ["Đang giao", "Xong", "Hủy", "Trả hàng", "Chờ xác nhận"],
                "amount": [10, 20, 30, 40, 50],
            })
        }
        detected, standardized = standardize_statuses(tables)
        con = _connect_duckdb(standardized, detected)
        cfg = infer_metric_config(con)
        self.assertTrue(cfg["ok"], cfg)
        self.assertEqual(
            con.execute(build_metric_sql(cfg, "orders", "2025-01-01", "2025-01-01")).fetchone()[0],
            2,
        )
        self.assertEqual(
            con.execute(build_metric_sql(cfg, "cancelled_orders", "2025-01-01", "2025-01-01")).fetchone()[0],
            1,
        )
        self.assertEqual(
            con.execute(build_metric_sql(cfg, "cancel_rate", "2025-01-01", "2025-01-01")).fetchone()[0],
            25.0,
        )

    def test_inference_requires_status_column(self):
        con = _connect_duckdb({
            "orders": pd.DataFrame({
                "ma_don": ["A"], "ngay": ["2025-01-01"], "amount": [10],
            })
        })
        config = infer_metric_config(con)
        self.assertFalse(config["ok"])
        self.assertEqual(config["reason"], "no status column")


if __name__ == "__main__":
    unittest.main()
