import unittest

import pandas as pd

from ai_agent_datamate.dataprocess.order_status import detect_status_column, standardize_statuses
from ai_agent_datamate.dataprocess.loader import _connect_duckdb


class OrderStatusTest(unittest.TestCase):
    def test_order_status_views_and_unknown_values_are_valid(self):
        tables = {
            "orders": pd.DataFrame(
                {"status": ["Hủy", "Đã hủy", "Trả hàng", "Xong", "Đang giao"]}
            )
        }
        detected, standardized = standardize_statuses(tables)
        self.assertEqual(detected["orders"].column, "status")
        self.assertEqual(
            standardized["orders"]["trang_thai_chuan"].tolist(),
            ["huy", "huy", "tra_hang", "hoan_thanh", "dang_giao"],
        )
        con = _connect_duckdb(standardized, detected)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM orders_hop_le").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM orders_khong_huy").fetchone()[0], 3)

    def test_status_phrases_and_unknown_status(self):
        tables = {
            "orders": pd.DataFrame(
                {
                    "status": [
                        "Đã hoàn hàng",
                        "Đã hoàn thành",
                        "Đã hủy",
                        "da hoan hang",
                        "ĐÃ HOÀN THÀNH",
                        "Chờ xác nhận",
                    ]
                }
            )
        }
        _, standardized = standardize_statuses(tables, business_id="biz_test")
        self.assertEqual(
            standardized["orders"]["trang_thai_chuan"].tolist(),
            ["tra_hang", "hoan_thanh", "huy", "tra_hang", "hoan_thanh", "khong_ro"],
        )

    def test_table_without_status_does_not_create_views(self):
        tables = {"orders": pd.DataFrame({"amount": [1, 2], "name": ["a", "b"]})}
        detected, standardized = standardize_statuses(tables)
        self.assertFalse(detected)
        con = _connect_duckdb(standardized, detected)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 2)
        with self.assertRaises(Exception):
            con.execute("SELECT * FROM orders_hop_le")

    def test_blank_status_uses_first_status_for_order(self):
        tables = {
            "orders": pd.DataFrame({
                "ma_don": ["ORD000001", None, "ORD000002"],
                "status": ["Đang giao", None, "Hủy"],
                "amount": [10, 20, 30],
            })
        }
        _, standardized = standardize_statuses(tables)
        self.assertEqual(
            standardized["orders"]["trang_thai_chuan"].tolist(),
            ["dang_giao", "dang_giao", "huy"],
        )

    def test_status_detector_rejects_identifiers_and_activity_state(self):
        self.assertIsNone(detect_status_column(pd.DataFrame({
            "ma_don": ["ORD000001", "ORD000002"],
        })))
        self.assertIsNone(detect_status_column(pd.DataFrame({
            "state": ["Đang hoạt động", "Đang hoạt động"],
        })))


if __name__ == "__main__":
    unittest.main()
