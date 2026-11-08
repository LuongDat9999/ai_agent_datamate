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

VALID_TYPES = frozenset({"number", "percent", "string", "date", "boolean", "list", "table", "unanswerable", "abstain"})


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
        text = str(value).strip().replace("/", "-")
        match = re.match(r"^(\d{4}-\d{1,2})(?:-(\d{1,2}))?", text)
        if not match:
            raise ValueError(f"Invalid date value: {value!r}")
        return f"{match.group(1) if month_only or not match.group(2) else f'{match.group(1)}-{int(match.group(2)):02d}'}"
    return result.strftime("%Y-%m" if month_only else "%Y-%m-%d")


def _month_question(question: str) -> bool:
    normalized = unicodedata.normalize("NFD", question.lower())
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    return bool(re.search(r"\b(thang|month)\b", normalized))


def infer_type(answer_type: str, columns: list[str] | None, rows: list[list[Any]] | None, question: str = "") -> str | None:
    if answer_type in {"unanswerable", "abstain"}:
        return answer_type
    rows = rows or []
    if answer_type in VALID_TYPES - {"unanswerable", "abstain"}:
        shape_ok = (
            (answer_type in {"number", "percent", "string", "date", "boolean"} and len(rows) == 1 and len(rows[0]) == 1)
            or (answer_type == "list" and bool(rows) and all(len(row) == 1 for row in rows))
            or (answer_type == "table" and bool(rows) and bool(columns))
        )
        if shape_ok:
            return answer_type
    if len(rows) == 1 and len(rows[0]) == 1:
        value = rows[0][0]
        if "phần trăm" in question.lower() or "%" in question or "tỷ lệ" in question.lower():
            return "percent"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return "number"
        if isinstance(value, (date, datetime)):
            return "date"
    if len(rows) > 1 and len(rows[0]) == 1:
        return "list"
    if len(rows) >= 1 and len(rows[0]) > 1:
        return "table"
    return answer_type if answer_type in VALID_TYPES else None


def to_answer(qid: str, decision: str, answer_type: str, columns: list[str] | None = None, rows: list[list[Any]] | None = None, *, question: str = "") -> dict[str, Any]:
    if not isinstance(qid, str) or not qid:
        raise ValueError("qid must be a non-empty string")
    if decision in {"unanswerable", "abstain"}:
        return {"id": qid, "type": decision}
    if decision != "answer":
        raise ValueError(f"Unknown decision: {decision}")
    rows = rows or []
    answer_type = infer_type(answer_type, columns, rows, question) or "abstain"
    if answer_type == "abstain":
        return {"id": qid, "type": "abstain"}
    if not rows:
        raise ValueError("answer decision requires at least one result row")
    values = [row[0] for row in rows]
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
        answer = [scalar(value) for value in values]
    elif answer_type == "table":
        answer = {"columns": list(columns or []), "rows": [[scalar(value) for value in row] for row in rows]}
    else:
        raise ValueError(f"Unsupported answer type: {answer_type}")
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


__all__ = ["fmt_date", "fmt_number", "fmt_percent", "infer_type", "scalar", "to_answer", "write_jsonl"]
