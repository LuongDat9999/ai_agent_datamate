"""Deterministic Vietnamese relative-date resolution."""

from __future__ import annotations

import calendar as _calendar
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class Period:
    start: str
    end: str
    label: str
    source: str = "relative"

    def as_tuple(self) -> tuple[str, str]:
        return self.start, self.end


def _date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _period(start: date, end: date, label: str, source: str = "relative") -> Period:
    if end < start:
        raise ValueError(f"period end precedes start: {start} > {end}")
    return Period(start.isoformat(), end.isoformat(), label, source)


def _month(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, _calendar.monthrange(year, month)[1])


def _shift_month(value: date, delta: int) -> tuple[date, date]:
    index = value.year * 12 + value.month - 1 + delta
    return _month(index // 12, index % 12 + 1)


def _previous_year_same_day(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _calendar_periods(calendar: Any, question: str) -> dict[str, Period]:
    if calendar is None:
        return {}
    rows: Iterable[Mapping[str, Any]]
    if isinstance(calendar, Mapping):
        rows = [calendar]
    elif hasattr(calendar, "to_dict"):
        rows = calendar.to_dict("records")
    else:
        rows = calendar
    normalized = question.casefold()
    result: dict[str, Period] = {}
    for row in rows:
        values = {str(k).casefold(): v for k, v in row.items()}
        text = " ".join(str(v) for v in values.values() if v is not None).casefold()
        if not any(term in normalized and term in text for term in ("tết", "tet", "mùng", "mung", "rằm", "ram", "30 tết", "30 tet")):
            continue
        date_value = next((v for k, v in values.items() if "date" in k or "ngày" in k or "ngay" in k), None)
        if date_value is None:
            continue
        try:
            day = _date(str(date_value))
        except ValueError:
            continue
        result["calendar_match"] = _period(day, day, "calendar_match", "calendar")
        break
    return result


def resolve(
    question: str,
    as_of: str | date,
    calendar: Any = None,
    rulebook: str = "",
) -> dict[str, Period]:
    """Resolve all recognized periods; all bounds are inclusive."""
    if not isinstance(question, str) or not question.strip():
        return {}
    today = _date(as_of)
    q = question.casefold()
    periods: dict[str, Period] = {}
    if re.search(r"\bhôm nay\b|\bhom nay\b", q):
        periods["today"] = _period(today, today, "today")
    if re.search(r"\bhôm qua\b|\bhom qua\b", q):
        day = today - timedelta(days=1)
        periods["yesterday"] = _period(day, day, "yesterday")
    if re.search(r"\bhôm kia\b|\bhom kia\b", q):
        day = today - timedelta(days=2)
        periods["day_before_yesterday"] = _period(day, day, "day_before_yesterday")

    week_start = 0 if "chủ nhật" not in rulebook.casefold() else 6
    if re.search(r"\btuần này\b|\btuan nay\b", q):
        start = today - timedelta(days=(today.weekday() - week_start) % 7)
        periods["current"] = _period(start, today, "current")
    if re.search(r"\btuần rồi\b|\btuần trước\b|\btuan roi\b|\btuan truoc\b", q):
        start = today - timedelta(days=(today.weekday() - week_start) % 7 + 7)
        periods["previous"] = _period(start, start + timedelta(days=6), "previous")

    if re.search(r"\btháng này\b|\bthang nay\b", q):
        start, _ = _month(today.year, today.month)
        periods["current"] = _period(start, today, "current")
    if re.search(r"\btháng trước\b|\btháng rồi\b|\bthang truoc\b|\bthang roi\b", q):
        start, end = _shift_month(today, -1)
        periods["previous"] = _period(start, end, "previous")
        if re.search(r"\bso với\b|\bso voi\b|\bso sánh\b|\bso sanh\b", q):
            current_start, _ = _month(today.year, today.month)
            periods["current"] = _period(current_start, today, "current")
            periods["same_period_last_year"] = _period(
                _previous_year_same_day(current_start),
                _previous_year_same_day(today),
                "same_period_last_year",
            )
    q = re.sub(r"\bt(?=(?:0?[1-9]|1[0-2])\s*(?:/|-)\s*20\d{2}\b)", "tháng ", q)
    month_match = re.search(
        r"\b(?:tháng|thang|thg)\s*(1[0-2]|0?[1-9])(?:\s*(?:/|-)\s*(\d{4})|\s+(?:năm|nam)\s+(\d{4})|\s+(20\d{2}))?\b",
        q,
    )
    slash_month_match = re.search(r"(?<![\d/])([1-9]|1[0-2])\s*/\s*(20\d{2})\b", q)
    if month_match:
        month = int(month_match.group(1))
        explicit_year = month_match.group(2) or month_match.group(3) or month_match.group(4)
        year = int(explicit_year or today.year)
        start, end = _month(year, month)
        periods["month"] = _period(
            start,
            end if explicit_year or year != today.year or month != today.month else today,
            "month",
            "explicit",
        )
    elif slash_month_match and not re.search(r"\b(?:quý|quy|q)\s*(?:1|2|3|4)\s*/\s*20\d{2}\b", q):
        month, year = int(slash_month_match.group(1)), int(slash_month_match.group(2))
        start, end = _month(year, month)
        periods["month"] = _period(start, end, "month", "explicit")

    quarter = re.search(
        r"\b(?:quý|quy|q)\s*(1|2|3|4)(?:\s*(?:/|-)\s*(\d{4})|\s+(?:năm|nam)\s+(\d{4}))?\b",
        q,
    )
    if quarter:
        number = int(quarter.group(1))
        year = int(quarter.group(2) or quarter.group(3) or today.year)
        start_month = 1 + (number - 1) * 3
        start, _ = _month(year, start_month)
        _, end = _month(year, start_month + 2)
        periods["quarter"] = _period(start, end, "quarter", "explicit")
    if re.search(r"\bquý này\b|\bquy nay\b", q):
        number = (today.month - 1) // 3 + 1
        start, _ = _month(today.year, 1 + (number - 1) * 3)
        _, end = _month(today.year, number * 3)
        periods["current"] = _period(start, today, "current")
    if re.search(r"\bquý trước\b|\bquy truoc\b", q):
        number = (today.month - 1) // 3
        year = today.year if number else today.year - 1
        number = number or 4
        start, _ = _month(year, 1 + (number - 1) * 3)
        _, end = _month(year, number * 3)
        periods["previous"] = _period(start, end, "previous")
    if re.search(r"\bnăm nay\b|\bnam nay\b", q):
        periods["current"] = _period(date(today.year, 1, 1), today, "current")
    if re.search(r"\bnăm ngoái\b|\bnam ngoai\b", q):
        periods["previous"] = _period(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), "previous")
    explicit_year = re.search(r"\b(?:năm|nam)\s+(20\d{2})\b", q)
    if explicit_year and "month" not in periods and "quarter" not in periods:
        year = int(explicit_year.group(1))
        periods["year"] = _period(date(year, 1, 1), date(year, 12, 31), "year", "explicit")

    relative = re.search(r"\b(\d+)\s*(ngày|ngay|tuần|tuan|tháng|thang)\s*(?:qua|gần đây|gan day)\b", q)
    if relative:
        amount, unit = int(relative.group(1)), relative.group(2)
        days = amount * (7 if unit.startswith("tu") else 1)
        if unit.startswith("th"):
            start, _ = _shift_month(today, -amount + 1)
        else:
            start = today - timedelta(days=days - 1)
        periods["recent"] = _period(start, today, "recent")

    if re.search(r"\bcùng kỳ năm ngoái\b|\bcung ky nam ngoai\b", q):
        current = periods.get("current")
        if current:
            start, end = _date(current.start), _date(current.end)
            periods["same_period_last_year"] = _period(_previous_year_same_day(start), _previous_year_same_day(end), "same_period_last_year")
    periods.update(_calendar_periods(calendar, q))
    return periods


def resolve_time(question: str, as_of_date: str | date, calendar: Any = None, rulebook: str = "") -> dict[str, Any]:
    periods = resolve(question, as_of_date, calendar, rulebook)
    today = _date(as_of_date)
    warnings = ["UNRESOLVED_TIME"] if not periods and re.search(
        r"\b(hôm|hom|tuần|tuan|tháng|thang|thg|quý|quy|q[1-4]|năm|nam|tết|tet)\b",
        question.casefold(),
    ) else []
    if any(_date(period.start) > today for period in periods.values()):
        warnings.append("FUTURE_PERIOD")
    return {
        "periods": {name: asdict(value) for name, value in periods.items()},
        "warnings": warnings,
    }


__all__ = ["Period", "resolve", "resolve_time"]