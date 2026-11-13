"""Diagnose workbook sheets against the loaded public view."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import openpyxl

try:
    from dataprocess import load_business
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dataprocess import load_business


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = ROOT / "data" / "train" / "data" / "biz_102" / "DonHang_ChiTiet.xlsx"


def _format(value: Any) -> str:
    return repr(value)


def _sheet_rows(path: Path) -> list[tuple[str, list[tuple[Any, ...]]]]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    result = []
    for worksheet in workbook.worksheets:
        rows = [tuple(row) for row in worksheet.iter_rows(values_only=True)]
        rows = [row for row in rows if any(value is not None for value in row)]
        result.append((worksheet.title, rows))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--business-dir", type=Path, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    business_dir = args.business_dir or args.workbook.parent
    context = load_business(business_dir, business_id=business_dir.name)
    loaded_views = {
        row[0]
        for row in context.con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name NOT LIKE '\\_%' ESCAPE '\\'"
        ).fetchall()
    }
    public_columns = {
        row[0] for row in context.con.execute("DESCRIBE donhang_chitiet").fetchall()
    }

    for sheet, rows in _sheet_rows(args.workbook):
        source = f"DonHang_ChiTiet_{sheet}".lower()
        public_rows = 0
        if "_nguon_bang" in public_columns:
            public_rows = context.con.execute(
                "SELECT COUNT(*) FROM donhang_chitiet WHERE _nguon_bang = ?",
                [source],
            ).fetchone()[0]
        raw_name = next(
            (name for name in loaded_views if name.endswith(f"_{sheet.lower()}_raw")),
            None,
        )
        loaded_rows = (
            context.con.execute(f'SELECT COUNT(*) FROM "{raw_name}"').fetchone()[0]
            if raw_name
            else 0
        )
        print(f"\n{sheet}: rows={len(rows)}")
        for index, row in enumerate(rows[:3], start=1):
            print(f"  row{index}_columns={[_format(value) for value in row]}")
        print(f"  in_donhang_chitiet={public_rows > 0} rows={public_rows}")
        print(f"  in_loaded_table_or_view={loaded_rows > 0} rows={loaded_rows}")
        if public_rows == 0:
            print("  first_5_data_rows:")
            for row in rows[1:6]:
                print(f"    {[_format(value) for value in row]}")
            date_values = [row[1] for row in rows[1:] if len(row) > 1 and row[1] is not None]
            print(
                "  date_column="
                f"type={type(date_values[0]).__name__ if date_values else 'EMPTY'} "
                f"samples={[_format(value) for value in date_values[:5]]}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
