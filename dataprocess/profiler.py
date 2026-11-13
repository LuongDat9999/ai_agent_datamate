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
    grain: str = "unknown"
    grain_confidence: float = 0.0
    roles: dict[str, dict[str, Any]] = field(default_factory=dict)
    measures: dict[str, str] = field(default_factory=dict)
    semantic_notes: list[str] = field(default_factory=list)


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


def _role_name(column: str) -> str:
    return vn_norm(column)


def infer_table_semantics(frame: pd.DataFrame) -> dict[str, Any]:
    """Infer reusable semantic metadata from names, types, repetition and arithmetic."""
    columns = [
        str(column) for column in frame.columns
        if not str(column).startswith("_") and str(column) != "is_total"
    ]
    normalized = {column: _role_name(column) for column in columns}
    roles: dict[str, dict[str, Any]] = {}

    def assign(column: str, role: str, confidence: float, evidence: str) -> None:
        current = roles.get(column)
        if current is None or confidence > current["confidence"]:
            roles[column] = {"role": role, "confidence": round(confidence, 3), "evidence": evidence}

    for column in columns:
        name = normalized[column]
        if any(token in name for token in ("ma don", "so don", "id don", "hoa don", "invoice", "order id")):
            distinct_ratio = frame[column].nunique(dropna=True) / max(frame[column].notna().sum(), 1)
            assign(column, "order_key", 0.9 if distinct_ratio < 0.95 else 0.65, "fuzzy identifier name and distinctness")
        if any(token in name for token in ("ngay", "date", "time", "thoi gian")):
            assign(column, "event_date", 0.85, "fuzzy date name")
        if any(token in name for token in ("trang thai", "status", "ket qua", "state")):
            assign(column, "status", 0.85, "fuzzy status name")
        if any(token in name for token in ("ten", "name", "san pham", "hang", "noi dung", "khach", "customer")):
            assign(column, "entity", 0.75, "fuzzy entity name")
        if any(token in name for token in ("so luong", "quantity", "qty", "sl")):
            assign(column, "quantity", 0.8, "fuzzy quantity name")
        if any(token in name for token in ("quy ra", "he so", "ti le", "ty le", "factor", "conversion")):
            assign(column, "conversion_factor", 0.8, "fuzzy conversion name")
        if any(token in name for token in ("don gia", "unit price", "gia ban", "gia san pham", "price")):
            assign(column, "unit_price", 0.8, "fuzzy unit-price name")
        if any(token in name for token in ("thanh tien", "tong tien", "amount", "total", "revenue")):
            assign(column, "line_amount", 0.8, "fuzzy amount name")

    numeric = [column for column in columns if pd.api.types.is_numeric_dtype(frame[column])]
    quantities = [column for column, info in roles.items() if info["role"] == "quantity" and column in numeric]
    prices = [column for column, info in roles.items() if info["role"] == "unit_price" and column in numeric]
    amounts = [column for column, info in roles.items() if info["role"] == "line_amount" and column in numeric]
    for price in prices:
        for quantity in quantities:
            for amount in amounts:
                sample = frame[[price, quantity, amount]].dropna()
                if sample.empty:
                    continue
                relative = ((sample[price] * sample[quantity] - sample[amount]).abs() /
                            sample[amount].abs().clip(lower=1))
                ratio = float((relative <= 0.01).mean())
                if ratio >= 0.8:
                    assign(amount, "line_amount", min(0.99, ratio), f"{price} * {quantity} ≈ {amount}")

    key_candidates = [column for column, info in roles.items() if info["role"] == "order_key"]
    key = key_candidates[0] if key_candidates else None
    if key is None:
        repeated = []
        for column in columns:
            non_null = frame[column].dropna()
            distinct_ratio = frame[column].nunique(dropna=True) / max(len(non_null), 1)
            inferred_role = roles.get(column, {}).get("role")
            name_hint = any(token in normalized[column] for token in ("order", "don", "hoa don", "invoice", "code", "id", "ma", "so"))
            if (
                distinct_ratio < 0.95
                and not pd.api.types.is_numeric_dtype(frame[column])
                and not pd.api.types.is_datetime64_any_dtype(frame[column])
                and inferred_role not in {"event_date", "status"}
            ):
                repeated.append((0 if name_hint else 1, distinct_ratio, column))
        key = min(repeated)[2] if repeated else None
        if key:
            assign(key, "order_key", 0.55, "repeated non-numeric candidate with fallback distinctness")
    if key:
        distinct_ratio = frame[key].nunique(dropna=True) / max(frame[key].notna().sum(), 1)
        grain = "line" if distinct_ratio < 0.95 else "order"
        grain_confidence = 0.9 if key in key_candidates else 0.55
    else:
        grain, grain_confidence = "unknown", 0.0
    measures = {
        "order_count": f"COUNT(DISTINCT {key})" if key else "unknown",
        "revenue": next((column for column in amounts), "unknown"),
        "base_quantity": next((f"{column} * {next((c for c, info in roles.items() if info['role'] == 'conversion_factor'), '1')}" for column in quantities), "unknown"),
    }
    return {
        "grain": grain,
        "grain_confidence": grain_confidence,
        "order_key": key,
        "roles": roles,
        "measures": measures,
    }


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
        lines.append(f"TABLE {name} ({profile.row_count} rows; grain={profile.grain}, confidence={profile.grain_confidence:.2f})")
        if profile.measures:
            lines.append(f"  MEASURES {profile.measures}")
        for column, info in profile.columns.items():
            detail = f"{info['dtype']}, null={info['null_ratio']:.1%}, distinct={info['distinct']}"
            if "values" in info:
                detail += f", values={info['values']}"
            if "min" in info:
                detail += f", range={info['min']}..{info['max']}"
            role = profile.roles.get(column)
            role_text = f", role={role['role']} ({role['confidence']:.2f})" if role else ""
            lines.append(f"  - {column}: {detail}{role_text}")
        if "ngay" in profile.columns:
            lines.append("  NOTE: Dùng cột `ngay` cho bộ lọc ngày của bảng này.")
        for note in profile.semantic_notes:
            lines.append(f"  NOTE: {note}")
    return "\n".join(lines)
