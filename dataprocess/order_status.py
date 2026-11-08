"""Detect and standardize order status columns without changing source values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .cleaner import vn_norm

STATUS_VALUES = {
    "huy": frozenset({"huy", "da huy", "huy don", "cancel", "cancelled"}),
    "tra_hang": frozenset({"tra hang", "tra lai", "hoan tra", "da tra", "hoan hang", "return", "returned"}),
    "dang_giao": frozenset({"dang giao", "cho giao", "dang van chuyen", "shipping"}),
}


@dataclass(frozen=True)
class StatusColumn:
    column: str
    value_mapping: dict[str, str]
    counts: dict[str, int]

    @property
    def prompt_text(self) -> str:
        mapping = ", ".join(f"{value!r}→{status}" for value, status in self.value_mapping.items())
        counts = ", ".join(f"{value!r}={count}" for value, count in self.counts.items())
        return f"cột {self.column}: {mapping}; số dòng: {counts}"


def detect_status_column(frame: pd.DataFrame) -> StatusColumn | None:
    for column in frame.columns:
        if column.startswith("_") or column == "is_total":
            continue
        series = frame[column].dropna()
        distinct = series.astype(str).unique().tolist()
        if len(distinct) > 12:
            continue
        mapping = {
            value: status
            for value in distinct
            for status, values in STATUS_VALUES.items()
            if vn_norm(value) in values
        }
        if mapping:
            counts = {value: int((series.astype(str) == value).sum()) for value in distinct}
            return StatusColumn(column, mapping, counts)
    return None


def standardize_statuses(
    tables: dict[str, pd.DataFrame],
) -> tuple[dict[str, StatusColumn], dict[str, pd.DataFrame]]:
    detected: dict[str, StatusColumn] = {}
    standardized: dict[str, pd.DataFrame] = {}
    for table_name, frame in tables.items():
        status = detect_status_column(frame)
        if status is None:
            standardized[table_name] = frame
            continue
        result = frame.copy()

        def normalize(value: Any) -> str:
            normalized = vn_norm(value)
            for raw_value, mapped in status.value_mapping.items():
                if vn_norm(raw_value) == normalized:
                    return mapped
            return "hoan_thanh"

        result["trang_thai_chuan"] = result[status.column].map(normalize)
        detected[table_name] = status
        standardized[table_name] = result
    return detected, standardized


def render_status_prompt(status_columns: dict[str, StatusColumn]) -> str:
    if not status_columns:
        return "Không có cột trạng thái được phát hiện."
    lines = ["QUY ƯỚC TRẠNG THÁI ĐƠN"]
    for table, status in status_columns.items():
        lines.append(f"- {table}: {status.prompt_text}.")
    lines.append(
        "Dùng *_hop_le cho doanh thu, số đơn, bán chạy, khách mua; "
        "*_khong_huy cho công nợ; dùng bảng gốc cho tỉ lệ đơn huỷ và giá bán ngày D."
    )
    return "\n".join(lines)


__all__ = ["STATUS_VALUES", "StatusColumn", "detect_status_column", "standardize_statuses", "render_status_prompt"]
