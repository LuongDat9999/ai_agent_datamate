import pandas as pd

from ai_agent_datamate.dataprocess.order_status import standardize_statuses
from ai_agent_datamate.dataprocess.loader import _connect_duckdb


def test_order_status_views_and_unknown_values_are_valid():
    tables = {"orders": pd.DataFrame({"status": ["Hủy", "Đã hủy", "Trả hàng", "Xong", "Đang giao"]})}
    detected, standardized = standardize_statuses(tables)
    assert detected["orders"].column == "status"
    assert standardized["orders"]["trang_thai_chuan"].tolist() == [
        "huy", "huy", "tra_hang", "hoan_thanh", "dang_giao"
    ]
    con = _connect_duckdb(standardized, detected)
    assert con.execute("SELECT COUNT(*) FROM orders_hop_le").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM orders_khong_huy").fetchone()[0] == 3


def test_table_without_status_does_not_create_views():
    tables = {"orders": pd.DataFrame({"amount": [1, 2], "name": ["a", "b"]})}
    detected, standardized = standardize_statuses(tables)
    assert not detected
    con = _connect_duckdb(standardized, detected)
    assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2
    try:
        con.execute("SELECT * FROM orders_hop_le")
    except Exception:
        return
    raise AssertionError("status view should not exist")
