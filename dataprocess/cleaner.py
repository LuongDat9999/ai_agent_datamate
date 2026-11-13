"""Deterministic cleaning helpers for DataMate source tables."""

from __future__ import annotations

import math
import os
import re
import unicodedata
import warnings
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd

TOTAL_PAT = re.compile(r"^\s*(cộng|tổng cộng|tổng|grand total|total|tổng ngày|cộng ngày|cộng tháng)\b", re.I)
UNIT = {"k": 1e3, "nghìn": 1e3, "ngàn": 1e3, "ngan": 1e3, "tr": 1e6, "triệu": 1e6, "trieu": 1e6, "tỷ": 1e9, "ty": 1e9}
PARSE_THRESHOLD = 0.8


def vn_norm(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value)).lower().replace("đ", "d")
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def snake_case(value: Any) -> str:
    return vn_norm(value).replace(" ", "_") or "column"


def unique_column_names(values: Iterable[Any]) -> list[str]:
    result, counts = [], {}
    for value in values:
        base = snake_case(value)
        counts[base] = counts.get(base, 0) + 1
        result.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return result


def parse_plain_number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    value = str(value)
    text = value.replace(" ", "").strip("()-")
    negative = value.strip().startswith("-") or (value.strip().startswith("(") and value.strip().endswith(")"))
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", text):
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", text):
        text = text.replace(",", "")
    elif re.fullmatch(r"\d+,\d+", text):
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def parse_money(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = unicodedata.normalize("NFC", str(value)).strip().lower()
    text = re.sub(r"(vnđ|vnd|đồng|₫|đ)\s*$", "", text).strip()
    match = re.fullmatch(r"(-?)\s*(\d+(?:[.,]\d+)?)\s*(tr|triệu|trieu|k|nghìn|ngàn|ngan|tỷ|ty)\s*(\d+)?", text)
    if match:
        sign, number, unit, suffix = match.groups()
        result = float(number.replace(",", ".")) * UNIT[unit]
        if suffix:
            result += float(f"0.{suffix}") * UNIT[unit]
        return -result if sign else result
    return parse_plain_number(text)


def parse_ratio(values: Iterable[Any], parser: Any) -> float:
    series = list(values)
    if not series:
        return 0.0
    non_empty = [value for value in series if pd.notna(value) and str(value).strip()]
    if not non_empty:
        return 0.0
    return sum(parser(value) is not None for value in non_empty) / len(non_empty)


def _parse_threshold() -> float:
    try:
        value = float(os.environ.get("DATAPROCESS_PARSE_THRESHOLD", PARSE_THRESHOLD))
    except ValueError as error:
        raise ValueError("DATAPROCESS_PARSE_THRESHOLD must be numeric") from error
    if not 0.0 <= value <= 1.0:
        raise ValueError("DATAPROCESS_PARSE_THRESHOLD must be between 0 and 1")
    return value


def _looks_like_entity(column: str, values: pd.Series) -> bool:
    if column.startswith("_") or column == "is_total":
        return False
    normalized = vn_norm(column)
    name_signal = any(token in normalized for token in ("ten", "name", "hang", "san pham", "khach", "customer", "doi tac"))
    return name_signal or (values.dtype == object and values.nunique(dropna=True) > 1)


def infer_date_order(values: Iterable[Any]) -> str:
    day_first = month_first = 0
    for value in values:
        match = re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})(?:[/\-.]\d{2,4})?\s*$", str(value))
        if match:
            first, second = int(match.group(1)), int(match.group(2))
            day_first += first > 12
            month_first += second > 12
    if day_first and not month_first:
        return "dmy"
    if month_first and not day_first:
        return "mdy"
    return "dmy"


def infer_date_format(values: Iterable[Any]) -> tuple[str, str]:
    """Infer a column-level date convention and return ``(label, order)``."""
    samples = [str(value).strip() for value in values if pd.notna(value) and str(value).strip()][:1000]
    if not samples:
        return "unknown", "dmy"
    if any(re.search(r"\b(?:thg|tháng|th)\b", value, re.I) for value in samples):
        return "vietnamese_month_text", "dmy"
    if any(re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", value) for value in samples):
        return "iso_or_year_first", "ymd"
    order = infer_date_order(samples)
    ambiguous = any(
        (match := re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})(?:[/\-.]\d{2,4})?\s*$", value))
        and int(match.group(1)) <= 12 and int(match.group(2)) <= 12
        for value in samples
    )
    return ("day_first" if order == "dmy" else "month_first") + ("_ambiguous" if ambiguous else ""), order


def parse_date_cell(value: Any, order: str = "dmy", default_year: int | None = None) -> pd.Timestamp | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(value).normalize()
    if isinstance(value, (int, float)) and 20_000 <= float(value) <= 80_000:
        return pd.Timestamp("1899-12-30") + pd.to_timedelta(float(value), unit="D")
    text = unicodedata.normalize("NFC", str(value)).strip()
    match = re.search(r"(?:ngày\s*)?(\d{1,2})\s*(?:thg|tháng|th)\s*(\d{1,2})(?:\s*(?:năm)?\s*(\d{4}))?", text, re.I)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
        return pd.Timestamp(int(year or default_year or 1900), month, day)
    match = re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\s*$", text)
    if not match:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
        return None if pd.isna(parsed) else pd.Timestamp(parsed).normalize()
    first, second = int(match.group(1)), int(match.group(2))
    year = int(match.group(3)) if match.group(3) else default_year
    if year is None:
        return None
    if year < 100:
        year += 2000
    day, month = (first, second) if order != "mdy" else (second, first)
    try:
        return pd.Timestamp(year, month, day)
    except ValueError:
        return None


def trim_grid(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.dropna(axis=0, how="all").dropna(axis=1, how="all").reset_index(drop=True)


def detect_header(frame: pd.DataFrame, max_rows: int = 15) -> int:
    best_index, best_score = 0, float("-inf")
    for index in range(min(max_rows, len(frame))):
        values = [v for v in frame.iloc[index].tolist() if pd.notna(v) and str(v).strip()]
        if not values:
            continue
        text_ratio = sum(not isinstance(v, (int, float)) for v in values) / len(values)
        score = len(values) + 2 * text_ratio
        if index:
            following = frame.iloc[index + 1 : min(len(frame), index + 21)]
            repeated = sum(
                any(value == candidate for candidate in following.iloc[:, column].tolist())
                for column, value in enumerate(frame.iloc[index].tolist())
                if pd.notna(value) and str(value).strip()
            )
            score -= repeated / max(len(values), 1)
        if score > best_score:
            best_index, best_score = index, score
    return best_index


def make_table(frame: pd.DataFrame, source: str, sheet: str | None = None) -> pd.DataFrame:
    frame = trim_grid(frame)
    if frame.empty:
        return frame
    header_row = detect_header(frame)
    data = frame.iloc[header_row + 1 :].copy()
    data.columns = unique_column_names(frame.iloc[header_row].tolist())
    data = trim_grid(data)
    data["_nguon_file"] = source
    if sheet is not None:
        data["_nguon_sheet"] = sheet
    return data.reset_index(drop=True)


def flag_totals(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    mask = pd.Series(False, index=result.index)
    for column in result.columns:
        if column.startswith("_"):
            continue
        mask |= result[column].astype(str).str.match(TOTAL_PAT, na=False)
    result["is_total"] = mask
    result["_total_validation"] = "not_applicable"
    if mask.any():
        total_rows = result[mask]
        detail_rows = result[~mask]
        numeric_columns = [
            column for column in result.columns
            if column not in {"is_total", "_total_validation"}
            and (
                pd.api.types.is_numeric_dtype(result[column])
                or parse_ratio(result[column], parse_money) >= _parse_threshold()
            )
        ]
        for column in numeric_columns:
            detail_sum = pd.Series(detail_rows[column]).map(parse_money).sum(min_count=1)
            for index in total_rows.index:
                total_value = parse_money(result.at[index, column])
                if pd.notna(total_value) and pd.notna(detail_sum):
                    result.at[index, "_total_validation"] = (
                        "matches" if math.isclose(float(total_value), float(detail_sum), rel_tol=0.01, abs_tol=0.01)
                        else "mismatch"
                    )
    return result


def ffill_group_cols(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    detail_cols = [c for c in result.columns if any(t in c for t in ("so_luong", "sl", "thanh_tien", "amount", "gia"))]
    if not detail_cols:
        return result
    has_detail = result[detail_cols].notna().any(axis=1)
    group_cols = [
        c for c in result.columns
        if not c.startswith("_") and result[c].isna().mean() > 0.3 and c not in detail_cols
        and pd.to_numeric(result[c], errors="coerce").notna().mean() < 0.6
    ]
    if group_cols:
        result.loc[has_detail, group_cols] = result.loc[has_detail, group_cols].ffill()
    return result


def normalize_columns(
    frame: pd.DataFrame,
    source: str = "<unknown>",
    sheet: str | None = None,
) -> pd.DataFrame:
    result = frame.copy()
    renamed = {}
    used = set()
    for column in result.columns:
        base = str(column) if str(column).startswith("_") else snake_case(column)
        candidate, suffix = base, 2
        while candidate in used:
            candidate, suffix = f"{base}_{suffix}", suffix + 1
        used.add(candidate)
        renamed[column] = candidate
    result.rename(columns=renamed, inplace=True)
    threshold = _parse_threshold()
    for column in list(result.columns):
        if column.startswith("_") or column == "is_total":
            continue
        values = result[column]
        name = vn_norm(column)
        money_named = any(token in name for token in ("tien", "gia", "doanh thu", "thanh tien", "amount", "no", "thu", "price", "cost", "total"))
        parsed = values.map(parse_money)
        if money_named and parse_ratio(values, parse_money) >= threshold:
            result[f"{column}_goc"] = values
            result[column] = parsed
        elif _looks_like_entity(column, values):
            result[f"{column}_norm"] = values.map(vn_norm)
        if any(token in name for token in ("ngay", "date", "time", "thoi gian")):
            format_label, order = infer_date_format(values)
            samples = [str(value) for value in values.dropna().head(5).tolist()]
            if format_label in {"unknown", "vietnamese_month_text"} or "ambiguous" in format_label:
                warnings.warn(
                    f"Date format inferred at column level: source={source!r}, "
                    f"sheet={sheet!r}, "
                    f"column={column!r}, format={format_label!r}, order={order!r}, "
                    f"samples={samples!r}",
                    RuntimeWarning,
                    stacklevel=2,
                )
            date_text = values.astype("string").str.strip()
            vietnamese = date_text.str.extract(
                r"^\s*(?:ngày\s*)?(\d{1,2})\s*(?:thg|tháng|th)\s*(\d{1,2})"
                r"(?:\s*(?:năm)?\s*(\d{4}))?\s*$",
                expand=True,
            )
            normalized_text = date_text.copy()
            vietnamese_mask = vietnamese[0].notna()
            if vietnamese_mask.any():
                years = vietnamese[2].fillna("1900").astype(str)
                normalized_text.loc[vietnamese_mask] = (
                    years.loc[vietnamese_mask]
                    + "-"
                    + vietnamese[1].str.zfill(2).loc[vietnamese_mask]
                    + "-"
                    + vietnamese[0].str.zfill(2).loc[vietnamese_mask]
                )
            dates = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
            if vietnamese_mask.any():
                dates.loc[vietnamese_mask] = pd.to_datetime(
                    normalized_text.loc[vietnamese_mask],
                    errors="coerce",
                    format="%Y-%m-%d",
                )
            other_mask = ~vietnamese_mask
            if other_mask.any():
                dates.loc[other_mask] = pd.to_datetime(
                    normalized_text.loc[other_mask],
                    errors="coerce",
                    dayfirst=order == "dmy",
                    format="mixed",
                )
            dates = dates.map(lambda value: value.normalize() if pd.notna(value) else None)
            if dates.notna().any():
                result[f"{column}_raw"] = values
                result["ngay"] = dates
        elif not money_named and parse_ratio(values, parse_plain_number) >= threshold:
            result[f"{column}_goc"] = values
            result[column] = values.map(lambda value: parse_plain_number(str(value)) if pd.notna(value) else None)
    return result


def clean_table(frame: pd.DataFrame, source: str, sheet: str | None = None) -> pd.DataFrame:
    return normalize_columns(
        ffill_group_cols(flag_totals(make_table(frame, source, sheet))),
        source=source,
        sheet=sheet,
    )
