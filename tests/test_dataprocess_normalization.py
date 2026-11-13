import unittest

import pandas as pd

from ai_agent_datamate.dataprocess.cleaner import clean_table, normalize_columns, parse_money
from ai_agent_datamate.dataprocess.loader import _connect_duckdb, _merge_compatible_tables
from ai_agent_datamate.dataprocess.order_status import standardize_statuses


class DataProcessNormalizationTests(unittest.TestCase):
    def test_money_parser_supports_units_and_grouping(self):
        self.assertEqual(parse_money("19tr990"), 19_990_000)
        self.assertEqual(parse_money("120k"), 120_000)
        self.assertEqual(parse_money("1,250,000"), 1_250_000)

    def test_mixed_entity_column_is_not_forced_to_numeric_and_gets_norm(self):
        frame = pd.DataFrame({"Tên hàng": ["A", "B", "Khách lẻ"], "Giá": ["10k", "20k", "không rõ"]})
        cleaned = normalize_columns(frame)
        self.assertEqual(cleaned["gia"].tolist(), ["10k", "20k", "không rõ"])
        self.assertNotIn("gia_goc", cleaned)
        self.assertEqual(cleaned["ten_hang_norm"].tolist(), ["a", "b", "khach le"])

    def test_total_is_flagged_and_validation_is_recorded(self):
        frame = pd.DataFrame([
            ["Tên", "Thành tiền"],
            ["A", "10"],
            ["B", "20"],
            ["Cộng ngày", "30"],
        ])
        cleaned = clean_table(frame, "source.csv")
        total = cleaned.loc[cleaned["is_total"]].iloc[0]
        self.assertTrue(bool(total["is_total"]))
        self.assertEqual(total["_total_validation"], "matches")

    def test_fragmented_tables_merge_by_normalized_column_set(self):
        first = pd.DataFrame({"item": ["a"], "value": [1], "_nguon_file": ["a"]})
        second = pd.DataFrame({"value": [2], "item": ["b"], "_nguon_file": ["b"]})
        merged = _merge_compatible_tables({"source_a": first, "source_b": second})
        masters = [frame for name, frame in merged.items() if name.startswith("master_")]
        self.assertEqual(len(masters), 1)
        self.assertEqual(masters[0]["value"].tolist(), [1, 2])

    def test_status_variants_are_grouped_without_erasing_source(self):
        tables = {
            "orders": pd.DataFrame({
                "status": ["hủy", "huỷ", "Xong", "Đang giao", "Trả hàng", "khách lẻ"],
            })
        }
        detected, standardized = standardize_statuses(tables)
        result = standardized["orders"]
        self.assertEqual(result["status"].tolist(), tables["orders"]["status"].tolist())
        self.assertEqual(result["trang_thai_nhom"].tolist(), ["huy", "huy", "hop_le", "hop_le", "tra_hang", "khac"])
        self.assertIn("hủy", detected["orders"].value_mapping)

    def test_date_name_is_exposed_as_date_on_public_view(self):
        con = _connect_duckdb({"sales": pd.DataFrame({"Ngày tháng": ["01/02/2026", "02/02/2026"]})})
        columns = {row[0]: row[1] for row in con.execute('DESCRIBE "sales"').fetchall()}
        self.assertEqual(columns["ngay"], "DATE")

    def test_timestamp_is_exposed_as_date_on_public_view(self):
        con = _connect_duckdb({"sales": pd.DataFrame({
            "created_at": pd.to_datetime(["2026-02-01 10:00", "2026-02-02 10:00"])
        })})
        columns = {row[0]: row[1] for row in con.execute('DESCRIBE "sales"').fetchall()}
        self.assertEqual(columns["ngay"], "DATE")


if __name__ == "__main__":
    unittest.main()
