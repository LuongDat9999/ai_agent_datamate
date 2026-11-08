"""Schema, capability and entity profiling for cleaned DataMate tables."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .cleaner import vn_norm


@dataclass
class TableProfile:
    name: str
    row_count: int
    columns: dict[str, dict[str, Any]]
    sample_rows: list[dict[str, Any]]
    source_files: list[str] = field(default_factory=list)


@dataclass
class EntityIndex:
    values: dict[str, dict[str, list[str]]] = field(default_factory=dict)


def profile_table(name: str, frame: pd.DataFrame) -> TableProfile:
    columns = {}
    for column in frame.columns:
        series = frame[column]
        info = {"dtype": str(series.dtype), "null_ratio": round(float(series.isna().mean()), 4), "distinct": int(series.nunique(dropna=True))}
        if pd.api.types.is_datetime64_any_dtype(series):
            info["min"] = str(series.min().date()) if series.notna().any() else None
            info["max"] = str(series.max().date()) if series.notna().any() else None
        elif info["distinct"] <= 30:
            info["values"] = [str(v) for v in series.dropna().unique().tolist()]
        columns[column] = info
    sources = frame["_nguon_file"].dropna().astype(str).unique().tolist() if "_nguon_file" in frame else []
    sample = frame.head(3).where(pd.notna(frame.head(3)), None).to_dict("records")
    return TableProfile(name, len(frame), columns, sample, sources)


def build_entity_index(tables: dict[str, pd.DataFrame]) -> EntityIndex:
    index = EntityIndex()
    tokens = ("ten", "hang", "san_pham", "noi_dung", "khach", "chi_nhanh", "nhan_vien")
    for table_name, frame in tables.items():
        for column in frame.columns:
            if column.startswith("_") or not any(token in column for token in tokens):
                continue
            values = [str(v) for v in frame[column].dropna().unique() if str(v).strip()]
            if values:
                index.values.setdefault(column, {})[table_name] = sorted(values)
    return index


def _has_column(tables: dict[str, pd.DataFrame], tokens: tuple[str, ...]) -> bool:
    return any(any(token in vn_norm(column) for token in tokens) for frame in tables.values() for column in frame.columns)


def detect_capabilities(tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
    item_level = 0
    for frame in tables.values():
        names = [vn_norm(c) for c in frame.columns]
        has_product = any(any(t in n for t in ("san pham", "ten hang", "hang hoa", "product")) for n in names)
        has_quantity = any(any(t in n for t in ("so luong", "sl", "quantity")) for n in names)
        item_level += has_product and has_quantity
    date_series = [frame[c].dropna() for frame in tables.values() for c in frame.columns if c == "ngay" or "ngay" in vn_norm(c)]
    dates = pd.concat(date_series) if date_series else pd.Series(dtype="datetime64[ns]")
    stock = [frame for name, frame in tables.items() if "ton" in vn_norm(name) or any("ton" in vn_norm(c) for c in frame.columns)]
    return {
        "has_item_level": bool(item_level),
        "has_cost": _has_column(tables, ("gia von", "gia nhap", "loi nhuan", "lai gop")),
        "has_daily_stock_history": any("ngay" in frame and frame["ngay"].nunique(dropna=True) >= 2 for frame in stock),
        "has_customer": _has_column(tables, ("khach", "customer")),
        "has_payment": _has_column(tables, ("thu tien", "da thu", "payment", "thanh toan")),
        "has_debt": _has_column(tables, ("cong no", "han no", "debt")),
        "time_granularity": "day" if dates.nunique() >= 2 else ("month" if dates.nunique() else None),
    }


def render_schema(profiles: dict[str, TableProfile]) -> str:
    lines = []
    for name, profile in profiles.items():
        lines.append(f"TABLE {name} ({profile.row_count} rows)")
        for column, info in profile.columns.items():
            detail = f"{info['dtype']}, null={info['null_ratio']:.1%}, distinct={info['distinct']}"
            if "values" in info:
                detail += f", values={info['values']}"
            if "min" in info:
                detail += f", range={info['min']}..{info['max']}"
            lines.append(f"  - {column}: {detail}")
    return "\n".join(lines)
