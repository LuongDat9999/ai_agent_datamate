"""Deterministic output normalization for the DataMate submission format."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable
from .type_inference import expected_type

VALID_TYPES = frozenset({"number", "percent", "string", "date", "boolean", "list", "table", "unanswerable", "abstain"})
VALUE_TYPES = VALID_TYPES - {"unanswerable", "abstain"}


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("value is not numeric")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("numeric value is not finite")
    return result


def scalar(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value) if value != value.to_integral() else int(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        return scalar(value.item())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def fmt_number(value: Any) -> int | float:
    number = _decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(number) if number == number.to_integral() else float(number)


def fmt_percent(value: Any) -> int | float:
    return fmt_number(value)


def fmt_date(value: Any, month_only: bool = False) -> str:
    if isinstance(value, datetime):
        result = value.date()
    elif isinstance(value, date):
        result = value
    else:
        text = str(value).strip()
        parts = re.fullmatch(r"(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})", text)
        if parts and len(parts.group(1)) <= 2:
            day, month, year = (int(parts.group(i)) for i in (1, 2, 3))
            result = date(year, month, day)
            return result.strftime("%Y-%m" if month_only else "%Y-%m-%d")
        normalized = text.replace("/", "-")
        match = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", normalized)
        if not match:
            raise ValueError(f"Invalid date value: {value!r}")
        year, month = int(match.group(1)), int(match.group(2))
        if month_only or match.group(3) is None:
            return f"{year:04d}-{month:02d}"
        return date(year, month, int(match.group(3))).strftime("%Y-%m-%d")
    return result.strftime("%Y-%m" if month_only else "%Y-%m-%d")


def _normalized_question(question: str) -> str:
    normalized = unicodedata.normalize("NFD", question.lower())
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def _month_question(question: str) -> bool:
    return bool(re.search(r"\b(thang|month)\b", _normalized_question(question)))


def _shape_supports(answer_type: str, columns: list[str] | None, rows: list[list[Any]]) -> bool:
    if answer_type == "percent":
        return len(rows) == 1 and len(rows[0]) == 1
    if answer_type in {"number", "string", "date"}:
        if not rows or not all(row for row in rows):
            return False
        if answer_type == "number" and any(
            len(row) > 1 and all(_is_number(value) for value in row if value is not None)
            for row in rows
        ):
            return False
        return True
    if answer_type == "boolean":
        return len(rows) == 1 and len(rows[0]) == 1
    if answer_type == "list":
        return bool(rows) and all(row and len(row) >= 1 for row in rows)
    if answer_type == "table":
        return bool(rows) and bool(columns) and all(len(row) == len(columns) for row in rows)
    return False


def _value_can_be_date(value: Any) -> bool:
    try:
        fmt_date(value)
    except (TypeError, ValueError):
        return False
    return True


def _is_number(value: Any) -> bool:
    try:
        _decimal(value)
    except (TypeError, ValueError, ArithmeticError):
        return False
    return True


def _selected_values(answer_type: str, rows: list[list[Any]]) -> list[Any]:
    if answer_type == "list":
        return [row[0] for row in rows]
    cells = [value for row in rows for value in row]
    if answer_type == "date":
        return [value for value in cells if _value_can_be_date(value)]
    if answer_type == "number":
        return [value for value in cells if _is_number(value)]
    return [value for value in cells if value is not None]


def infer_type(answer_type: str, columns: list[str] | None, rows: list[list[Any]] | None, question: str = "") -> str | None:
    if answer_type in {"unanswerable", "abstain"}:
        return answer_type
    rows = rows or []
    if answer_type not in VALUE_TYPES:
        return None
    expected = expected_type(question)
    selected = expected or answer_type
    if expected and expected != answer_type:
        if not _shape_supports(expected, columns, rows):
            return None
    if not _shape_supports(selected, columns, rows):
        return None
    values = _selected_values(selected, rows)
    if not values:
        return None
    if selected == "date" and not _value_can_be_date(values[0]):
        return None
    if selected == "boolean" and not isinstance(rows[0][0], bool):
        return None
    return selected


def to_answer(qid: str, decision: str, answer_type: str, columns: list[str] | None = None, rows: list[list[Any]] | None = None, *, question: str = "", reason: str | None = None) -> dict[str, Any]:
    if not isinstance(qid, str) or not qid:
        raise ValueError("qid must be a non-empty string")
    if decision in {"unanswerable", "abstain"}:
        record = {"id": qid, "type": decision}
        if decision == "unanswerable" and reason is not None:
            record["reason"] = str(reason)
        return record
    if decision != "answer":
        raise ValueError(f"Unknown decision: {decision}")
    rows = rows or []
    answer_type = infer_type(answer_type, columns, rows, question) or "abstain"
    if answer_type == "abstain":
        return {"id": qid, "type": "abstain"}
    if not rows:
        raise ValueError("answer decision requires at least one result row")
    values = _selected_values(answer_type, rows)
    try:
        if answer_type == "number":
            answer = fmt_number(values[0])
        elif answer_type == "percent":
            answer = fmt_percent(values[0])
        elif answer_type == "string":
            answer = unicodedata.normalize("NFC", str(values[0]))
        elif answer_type == "date":
            answer = fmt_date(values[0], _month_question(question))
        elif answer_type == "boolean":
            answer = bool(values[0])
        elif answer_type == "list":
            answer = [scalar(row[0]) for row in rows]
        elif answer_type == "table":
            answer = {"columns": list(columns or []), "rows": [[scalar(value) for value in row] for row in rows]}
        else:
            return {"id": qid, "type": "abstain"}
    except (TypeError, ValueError, ArithmeticError):
        return {"id": qid, "type": "abstain"}
    return {"id": qid, "type": answer_type, "answer": answer}


def write_jsonl(records: Iterable[dict[str, Any]], path: str | Path) -> int:
    """Write one flushed JSON object per line and return the number written."""
    count = 0
    with Path(path).open("w", encoding="utf-8") as handle:
        for record in records:
            if not isinstance(record, dict) or not record.get("id"):
                raise ValueError("each output record needs a non-empty id")
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            count += 1
    return count


__all__ = [
    "expected_type", "fmt_date", "fmt_number", "fmt_percent", "infer_type",
    "scalar", "to_answer", "write_jsonl",
]
