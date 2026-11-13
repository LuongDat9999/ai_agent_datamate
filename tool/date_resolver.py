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

    @property
    def end_exclusive(self) -> str:
        return (_date(self.end) + timedelta(days=1)).isoformat()


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
    if re.search(r"\bhôm nay\b|\bhom nay\b|\bbữa nay\b|\bbua nay\b", q):
        periods["today"] = _period(today, today, "today")
    if re.search(r"\bhôm qua\b|\bhom qua\b|\bhqua\b|\bbữa qua\b|\bbua qua\b", q):
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
    elif slash_month_match and not re.search(r"\b(?:quý|quy|quys|qui|q)\s*(?:1|2|3|4)\s*/\s*20\d{2}\b", q):
        month, year = int(slash_month_match.group(1)), int(slash_month_match.group(2))
        start, end = _month(year, month)
        periods["month"] = _period(start, end, "month", "explicit")

    quarter = re.search(
        r"\b(?:quý|quy|quys|qui|q)\s*(1|2|3|4)(?:\s*(?:/|-)\s*(\d{4})|\s+(?:năm|nam)\s+(\d{4}))?\b",
        q,
    )
    if quarter:
        number = int(quarter.group(1))
        year = int(quarter.group(2) or quarter.group(3) or today.year)
        start_month = 1 + (number - 1) * 3
        start, _ = _month(year, start_month)
        _, end = _month(year, start_month + 2)
        periods["quarter"] = _period(start, end, "quarter", "explicit")
    if re.search(r"\b(?:quý|quy|quys|qui) này\b|\b(?:quy|quys|qui) nay\b", q):
        number = (today.month - 1) // 3 + 1
        start, _ = _month(today.year, 1 + (number - 1) * 3)
        _, end = _month(today.year, number * 3)
        periods["current"] = _period(start, today, "current")
    if re.search(r"\b(?:quý|quy|quys|qui) trước\b|\b(?:quy|quys|qui) truoc\b", q):
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
    explicit = next(
        (period for period in periods.values() if period.source == "explicit"),
        None,
    )
    compare_period = None
    comparison = re.search(r"\bso\s*(?:với|voi|vs)\b|\bso\s+sánh\s+(?:với|voi)\b", q)
    if comparison:
        comparison_text = q[comparison.end():].split(",", 1)[0]
        quarter_match = re.search(
            r"\b(?:quý|quy|quys|qui|q)\s*(1|2|3|4)(?:\s*(?:/|-)\s*(\d{4})|\s+(?:năm|nam)\s+(\d{4}))?\b",
            comparison_text,
        )
        month_match = re.search(
            r"\b(?:tháng|thang|thg)\s*(1[0-2]|0?[1-9])(?:\s*(?:/|-)\s*(\d{4})|\s+(?:năm|nam)\s+(\d{4})|\s+(20\d{2}))?\b",
            comparison_text,
        )
        year_match = re.search(r"\b(?:năm|nam)\s+(20\d{2})\b", comparison_text)
        if quarter_match:
            number = int(quarter_match.group(1))
            year = int(quarter_match.group(2) or quarter_match.group(3) or today.year)
            start_month = 1 + (number - 1) * 3
            start, _ = _month(year, start_month)
            _, end = _month(year, start_month + 2)
            compare_period = _period(start, end, "compare", "explicit")
        elif month_match:
            month = int(month_match.group(1))
            explicit_year = month_match.group(2) or month_match.group(3) or month_match.group(4)
            year = int(explicit_year or today.year)
            start, end = _month(year, month)
            compare_period = _period(start, end, "compare", "explicit")
        elif year_match:
            year = int(year_match.group(1))
            compare_period = _period(date(year, 1, 1), date(year, 12, 31), "compare", "explicit")
    comparison_head = q[comparison.end():].split(",", 1)[0] if comparison else ""
    unparsed_comparison_period = bool(
        comparison
        and re.search(r"\b(?:tháng|thang|thg|quý|quy|quys|qui|q|năm|nam)\b", comparison_head)
        and compare_period is None
        and not re.search(
            r"\b(?:trước|truoc|liền trước|lien truoc|trước đó|truoc do|rồi|roi|ngoái|ngoai)\b",
            comparison_head,
        )
    )
    compare_trigger = bool(re.search(
        r"\bso\s*(?:với|voi|vs)\b|"
        r"\bso\s+sánh\s+(?:với|voi)\b|"
        r"\b(?:tăng|tang)\s*(?:hay|hoặc|hoac)?\s*(?:giảm|giam)\b|"
        r"\b(?:hơn|hon)\s*(?:hay|hoặc|hoac)?\s*(?:kém|kem)\b|"
        r"\b(?:thay\s*đổi|thay\s*doi)\b|"
        r"\b(?:tháng|thang)\s+(?:liền\s+trước|lien\s+truoc|trước\s+đó|truoc\s+do)\b|"
        r"\b(?:cùng\s*kỳ|cùng\s*tháng|cung\s*ky|cung\s*thang)\b|"
        r"\b(?:cùng\s*kỳ\s*năm\s*ngoái|cung\s*ky\s*nam\s*ngoai|"
        r"so\s*(?:với|voi|vs)\s*năm\s*ngoái|so\s*voi\s*nam\s*ngoai)\b",
        q,
    ))
    previous_trigger = bool(re.search(
        r"\b(?:tháng|thang)\s+(?:trước|truoc|liền\s+trước|lien\s+truoc|trước\s+đó|truoc\s+do|rồi|roi)\b|"
        r"\b(?:quý|quy|quys|qui)\s+(?:trước|truoc)\b|"
        r"\b(?:tuần|tuan|năm|nam)\s+(?:trước|truoc|rồi|roi)\b",
        q,
    ))
    relative_target = None
    if explicit is None:
        target_patterns = (
            ("month", r"\btháng này\b|\bthang nay\b"),
            ("week", r"\btuần này\b|\btuan nay\b"),
            ("quarter", r"\bquý này\b|\bquy nay\b"),
            ("year", r"\bnăm nay\b|\bnam nay\b"),
        )
        target_label = next((label for label, pattern in target_patterns if re.search(pattern, q)), None)
        if target_label:
            relative_target = periods.get("current")
    target_period = explicit or relative_target
    same_year = bool(re.search(
        r"\b(?:cùng\s*kỳ|cùng\s*tháng|cung\s*ky|cung\s*thang)"
        r"(?:\s+(?:năm|nam)\s+(?:trước|truoc|ngoái|ngoai))?\b|"
        r"\b(?:cùng\s*kỳ\s*năm\s*ngoái|cung\s*ky\s*nam\s*ngoai|"
        r"so\s*(?:với|voi|vs)\s*năm\s*ngoái|so\s*voi\s*nam\s*ngoai)\b",
        q,
    ))
    if target_period and compare_trigger and not unparsed_comparison_period and (
        compare_period or previous_trigger or same_year
    ):
        target_start, target_end = _date(target_period.start), _date(target_period.end)
        periods["target"] = _period(target_start, target_end, "target", "explicit")
        if compare_period:
            compare_start, compare_end = _date(compare_period.start), _date(compare_period.end)
        elif same_year and target_period.label == "year":
            compare_start = date(target_start.year - 1, 1, 1)
            compare_end = date(target_start.year - 1, 12, 31)
        elif same_year:
            compare_start = _previous_year_same_day(target_start)
            compare_end = _previous_year_same_day(target_end)
        elif target_period.label == "month" or re.search(r"\b(?:tháng|thang)\b", q):
            compare_start, compare_end = _shift_month(target_start, -1)
        elif target_period.label == "quarter" or re.search(r"\b(?:quý|quy|quys|qui)\b", q):
            compare_start = _shift_month(target_start, -3)[0]
            _, compare_end = _shift_month(target_start, -1)
        elif target_period.label == "current" and re.search(r"\b(?:tuần|tuan)\b", q):
            compare_start = target_start - timedelta(days=7)
            compare_end = target_end - timedelta(days=7)
        else:
            compare_start = date(target_start.year - 1, target_start.month, target_start.day)
            compare_end = date(target_end.year - 1, target_end.month, target_end.day)
        periods["compare"] = _period(compare_start, compare_end, "compare", "relative")
        if explicit is not None:
            periods.pop(explicit.label, None)
        for name in ("current", "previous", "same_period_last_year"):
            periods.pop(name, None)
    return periods


def resolve_time(question: str, as_of_date: str | date, calendar: Any = None, rulebook: str = "") -> dict[str, Any]:
    periods = resolve(question, as_of_date, calendar, rulebook)
    today = _date(as_of_date)
    warnings = ["UNRESOLVED_TIME"] if not periods and re.search(
        r"\b(hôm|hom|tuần|tuan|tháng|thang|thg|quý|quy|quys|qui|q[1-4]|năm|nam|tết|tet)\b",
        question.casefold(),
    ) else []
    if any(_date(period.start) > today for period in periods.values()):
        warnings.append("FUTURE_PERIOD")
    return {
        "periods": {
            name: {**asdict(value), "end_exclusive": value.end_exclusive}
            for name, value in periods.items()
        },
        "warnings": warnings,
    }


__all__ = ["Period", "resolve", "resolve_time"]