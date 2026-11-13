"""Detect and standardize order status columns without changing source values."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
import re

import pandas as pd

from .cleaner import vn_norm

STATUS_VALUES = {
    "huy": frozenset({"huy", "da huy", "huy don", "cancel", "cancelled"}),
    "tra_hang": frozenset({
        "tra hang", "doi tra", "tra lai", "hoan tra", "hoan hang", "hoan tien",
        "da tra", "return", "returned",
    }),
    "dang_giao": frozenset({"dang giao", "cho giao", "dang van chuyen", "shipping"}),
    "hoan_thanh": frozenset({"xong", "hoan thanh", "completed", "done"}),
}
_LOGGER = logging.getLogger(__name__)
_STATUS_PHRASES = {
    "tra_hang": ("hoan hang", "hoan tra", "tra hang", "doi tra", "hoan tien", "tra lai", "da tra", "returned", "return"),
    "hoan_thanh": ("da hoan thanh", "hoan thanh", "completed", "done", "xong"),
    "dang_giao": ("dang van chuyen", "dang giao", "cho giao", "shipping"),
    "huy": ("da huy", "huy don", "cancelled", "cancel", "huy"),
}
_ORDERED_STATUS_PHRASES = tuple(
    (phrase, status)
    for status, phrases in _STATUS_PHRASES.items()
    for phrase in sorted(phrases, key=len, reverse=True)
)
VALID_STATUS_GROUP = "hop_le"


@dataclass(frozen=True)
class StatusColumn:
    column: str
    value_mapping: dict[str, str]
    counts: dict[str, int]
    group_mapping: dict[str, str] | None = None

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
        if len(distinct) > 30:
            continue
        if any(re.fullmatch(r"[A-Za-z]+\d+", value.replace(" ", "")) for value in distinct):
            continue
        if any(
            "đang hoạt động" in vn_norm(value) or vn_norm(value) == "ma don hang"
            for value in distinct
        ):
            continue
        mapping = {value: status for value in distinct if (status := _classify_status(value))}
        if mapping:
            counts = {value: int((series.astype(str) == value).sum()) for value in distinct}
            return StatusColumn(column, mapping, counts)
    return None


def status_vocabulary_from_rulebook(rulebook: str) -> set[str]:
    """Return normalized status words explicitly present in the active rulebook."""
    words = set()
    for match in re.finditer(
        r"(?i)\b(?:hủy|huỷ|trả hàng|trả lại|hoàn trả|hoàn hàng|hoàn tiền|đang giao|chờ giao|xong|hoàn thành)\b",
        rulebook,
    ):
        words.add(vn_norm(match.group(0)))
    return words


def _classify_status(value: Any) -> str | None:
    normalized = vn_norm(value)
    for phrase, status in _ORDERED_STATUS_PHRASES:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized):
            return status
    return None


def standardize_statuses(
    tables: dict[str, pd.DataFrame],
    rulebook: str = "",
    business_id: str | None = None,
) -> tuple[dict[str, StatusColumn], dict[str, pd.DataFrame]]:
    detected: dict[str, StatusColumn] = {}
    standardized: dict[str, pd.DataFrame] = {}
    for table_name, frame in tables.items():
        status = detect_status_column(frame)
        if status is None:
            standardized[table_name] = frame
            continue
        result = frame.copy()
        order_columns = [
            column for column in result.columns
            if any(token in vn_norm(column) for token in ("ma don", "so don", "don hang", "order", "invoice"))
        ]
        if order_columns:
            result[order_columns[0]] = result[order_columns[0]].ffill()
            result[status.column] = result.groupby(order_columns[0], dropna=False)[status.column].ffill()

        def normalize(value: Any) -> str:
            mapped = _classify_status(value)
            if mapped is not None:
                return mapped
            return "khong_ro"

        result["trang_thai_chuan"] = result[status.column].map(normalize)
        unknown_values = {
            value for value in result[status.column].dropna().unique()
            if _classify_status(value) is None
        }
        for value in sorted(unknown_values, key=str):
            _LOGGER.warning(
                "Unmapped order status value %r (business_id=%s)",
                value,
                business_id or "<unknown>",
            )
        known_values = set(status.value_mapping)
        result["trang_thai_nhom"] = result[status.column].map(
            lambda raw: (
                "huy" if status.value_mapping.get(raw) == "huy"
                else "tra_hang" if status.value_mapping.get(raw) == "tra_hang"
                else "hop_le" if raw in known_values and status.value_mapping.get(raw) in {"hoan_thanh", "dang_giao"}
                else "khac"
            )
        )
        if rulebook:
            known = status_vocabulary_from_rulebook(rulebook)
            result.loc[
                result[status.column].map(vn_norm).isin(known) & result["trang_thai_nhom"].eq("khac"),
                "trang_thai_nhom",
            ] = "hop_le"
        detected[table_name] = StatusColumn(
            status.column, status.value_mapping, status.counts,
            {raw: ("huy" if mapped == "huy" else "tra_hang" if mapped == "tra_hang" else "hop_le")
             for raw, mapped in status.value_mapping.items()},
        )
        standardized[table_name] = result
    return detected, standardized


def render_status_prompt(
    status_columns: dict[str, StatusColumn],
    tables: dict[str, pd.DataFrame] | None = None,
) -> str:
    if not status_columns:
        return "Không có cột trạng thái được phát hiện."
    values: set[str] = set()
    for table, status in status_columns.items():
        values.update(status.value_mapping.values())
        frame = (tables or {}).get(table)
        if frame is not None and "trang_thai_chuan" in frame:
            values.update(frame["trang_thai_chuan"].dropna().astype(str))
    labels = ", ".join(sorted(values))
    return (
        "QUY ƯỚC TRẠNG THÁI ĐƠN: cột trang_thai_chuan; "
        f"nhãn chuẩn có trong dữ liệu: {{{labels}}}. "
        "doanh thu và số đơn tính mọi đơn có trang_thai_chuan khác 'huy' và 'tra_hang' "
        "(đơn dang_giao vẫn tính); đếm đơn huỷ dùng trang_thai_chuan = 'huy'; "
        "đếm số đơn luôn dùng COUNT(DISTINCT <cột mã đơn>), không COUNT(*); không dùng cột trạng thái gốc."
    )


__all__ = [
    "STATUS_VALUES", "StatusColumn", "detect_status_column",
    "standardize_statuses", "status_vocabulary_from_rulebook", "render_status_prompt",
]
