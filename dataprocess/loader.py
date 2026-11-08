"""DataMate module 1: load, clean, profile and expose a business dataset."""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import openpyxl

import pandas as pd

from .cleaner import clean_table
from .order_status import StatusColumn, render_status_prompt, standardize_statuses
from .profiler import EntityIndex, TableProfile, build_entity_index, detect_capabilities, profile_table, render_schema

SUPPORTED_SUFFIXES = {".csv", ".xlsx", ".xls", ".sqlite", ".db"}


@dataclass
class ScannedBusiness:
    data: list[Path]
    readme_text: str = ""


@dataclass
class BusinessContext:
    business_id: str
    con: Any
    tables: dict[str, TableProfile]
    readme_text: str
    entity_index: EntityIndex
    capabilities: dict[str, Any]
    schema_prompt: str
    warnings: list[str] = field(default_factory=list)
    status_columns: dict[str, StatusColumn] = field(default_factory=dict)
    status_prompt: str = ""


def scan_business(dir_path: str | Path) -> ScannedBusiness:
    path = Path(dir_path)
    if not path.is_dir():
        raise FileNotFoundError(f"Business directory does not exist: {path}")
    data, readme = [], ""
    for item in sorted(path.iterdir(), key=lambda p: p.name.lower()):
        if item.name.lower() == "readme.md":
            readme = item.read_text(encoding="utf-8", errors="ignore")
        elif item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES:
            data.append(item)
    return ScannedBusiness(data, readme)


def _csv_delimiter(path: Path) -> str:
    sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _read_csv(path: Path) -> list[tuple[str, str | None, pd.DataFrame]]:
    errors = []
    for encoding in ("utf-8-sig", "utf-8", "cp1258"):
        try:
            frame = pd.read_csv(path, header=None, dtype=str, sep=_csv_delimiter(path), encoding=encoding)
            return [(path.name, None, frame)]
        except (UnicodeDecodeError, pd.errors.ParserError) as error:
            errors.append(str(error))
    raise ValueError(f"Cannot read CSV {path}: {'; '.join(errors)}")


def _read_excel(path: Path) -> list[tuple[str, str | None, pd.DataFrame]]:
    if path.suffix.lower() == ".xls":
        return [
            (path.name, sheet, pd.read_excel(path, sheet_name=sheet, header=None, dtype=object))
            for sheet in pd.ExcelFile(path).sheet_names
        ]
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True, read_only=False)
    result = []
    for worksheet in workbook.worksheets:
        for merged in list(worksheet.merged_cells.ranges):
            value = worksheet.cell(merged.min_row, merged.min_col).value
            worksheet.unmerge_cells(str(merged))
            for row in worksheet.iter_rows(
                min_row=merged.min_row,
                max_row=merged.max_row,
                min_col=merged.min_col,
                max_col=merged.max_col,
            ):
                for cell in row:
                    cell.value = value
        result.append((path.name, worksheet.title, pd.DataFrame(worksheet.values)))
    return result


def _read_sqlite(path: Path) -> list[tuple[str, str | None, pd.DataFrame]]:
    result = []
    with sqlite3.connect(path) as connection:
        names = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (name,) in names:
            result.append((path.name, name, pd.read_sql_query(f'SELECT * FROM "{name}"', connection)))
    return result


def _read_source(path: Path) -> list[tuple[str, str | None, pd.DataFrame]]:
    if path.suffix.lower() == ".csv":
        return _read_csv(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return _read_excel(path)
    return _read_sqlite(path)


def _safe_table_name(value: str, used: set[str]) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower() or "table"
    if name[0].isdigit():
        name = f"t_{name}"
    candidate, suffix = name, 2
    while candidate in used:
        candidate, suffix = f"{name}_{suffix}", suffix + 1
    used.add(candidate)
    return candidate


def _connect_duckdb(tables: dict[str, pd.DataFrame], status_columns: dict[str, StatusColumn] | None = None) -> Any:
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("SET enable_external_access=false")
    con.execute("SET threads=4")
    for name, frame in tables.items():
        con.register(f"_raw_{name}", frame)
        con.execute(f'CREATE TABLE "{name}_raw" AS SELECT * FROM "_raw_{name}"')
        if "is_total" in frame.columns:
            public_select = "* EXCLUDE (is_total)"
            total_filter = "WHERE NOT COALESCE(is_total, FALSE)"
        else:
            public_select = "*"
            total_filter = ""
        con.execute(f'CREATE VIEW "{name}" AS SELECT {public_select} FROM "{name}_raw" {total_filter}')
        if status_columns and name in status_columns:
            con.execute(f'CREATE VIEW "{name}_hop_le" AS SELECT * FROM "{name}" WHERE trang_thai_chuan NOT IN (\'huy\', \'tra_hang\')')
            con.execute(f'CREATE VIEW "{name}_khong_huy" AS SELECT * FROM "{name}" WHERE trang_thai_chuan <> \'huy\'')
        con.unregister(f"_raw_{name}")
    return con


def load_business(dir_path: str | Path, business_id: str | None = None) -> BusinessContext:
    scanned = scan_business(dir_path)
    cleaned, used_names = {}, set()
    for source in scanned.data:
        for file_name, sheet, raw in _read_source(source):
            if raw.empty:
                continue
            name_source = f"{Path(file_name).stem}_{sheet}" if sheet else Path(file_name).stem
            table = clean_table(raw, file_name, sheet)
            if not table.empty:
                cleaned[_safe_table_name(name_source, used_names)] = table
    if not cleaned:
        raise ValueError(f"No readable data tables found in {dir_path}")
    status_columns, cleaned = standardize_statuses(cleaned)
    profiles = {name: profile_table(name, frame) for name, frame in cleaned.items()}
    for name in status_columns:
        frame = cleaned[name]
        for suffix, predicate in (("_hop_le", lambda values: ~values.isin({"huy", "tra_hang"})),
                                  ("_khong_huy", lambda values: values != "huy")):
            view_frame = frame[predicate(frame["trang_thai_chuan"])]
            profiles[name + suffix] = profile_table(name + suffix, view_frame)
    return BusinessContext(
        business_id=business_id or Path(dir_path).name,
        con=_connect_duckdb(cleaned, status_columns),
        tables=profiles,
        readme_text=scanned.readme_text,
        entity_index=build_entity_index(cleaned),
        capabilities=detect_capabilities(cleaned),
        schema_prompt=render_schema(profiles),
        status_columns=status_columns,
        status_prompt=render_status_prompt(status_columns),
    )

def read_excel_grid(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    grids = {}
    for ws in wb.worksheets:
        for rng in list(ws.merged_cells.ranges):
            top_left = ws.cell(rng.min_row, rng.min_col).value
            ws.unmerge_cells(str(rng))
            for row in ws.iter_rows(min_row=rng.min_row, max_row=rng.max_row,
                                    min_col=rng.min_col, max_col=rng.max_col):
                for c in row:
                    c.value = top_left
        grids[ws.title] = pd.DataFrame(ws.values)      # header=None
    return grids