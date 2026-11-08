"""Result sanity checks and self-consistency voting for DataMate."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from ..llm.generator import Candidate
from ..sqlrunner.validator import HARD_FLAGS

SOFT_FLAGS = frozenset({"NO_DATE_FILTER", "NO_STATUS_FILTER", "UNIT_MISMATCH"})
HEAVY_FLAGS = HARD_FLAGS | frozenset({"SHAPE", "PCT_RANGE", "UNKNOWN_ENTITY", "JOIN_MISMATCH", "EMPTY"})


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value)).lower().replace("đ", "d")
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def canonical(cols: list[str] | None, rows: list[list[Any]] | None, answer_type: str) -> Any:
    """Create a stable, JSON-independent key for voting."""
    if not rows:
        return (answer_type, None)

    def scalar(value: Any) -> Any:
        if isinstance(value, Decimal):
            return round(float(value), 2)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return round(float(value), 2)
        return _norm(value) if isinstance(value, str) else value

    if answer_type == "table":
        return ("table", tuple(cols or ()), tuple(sorted(tuple(scalar(x) for x in row) for row in rows)))
    if answer_type == "list":
        return ("list", tuple(scalar(row[0]) for row in rows if row))
    return (answer_type, scalar(rows[0][0]))


@dataclass(frozen=True)
class CheckReport:
    flags: tuple[str, ...]
    supported: bool = True


def _intent_support(question: str, capabilities: dict[str, Any]) -> tuple[bool, bool]:
    q = _norm(question)
    hard = (
        (bool(re.search(r"\b(lợi nhuận|loi nhuan|lãi|lai|biên lợi nhuận|bien loi nhuan)\b", q)) and not capabilities.get("has_cost"))
        or (bool(re.search(r"\b(bán chạy|ban chay|top .*sản phẩm|top .*san pham|mặt hàng nào|mat hang nao)\b", q)) and not capabilities.get("has_item_level"))
        or (bool(re.search(r"\btồn\b|\bton\b", q)) and bool(re.search(r"\b(hôm qua|hom qua|ngày|ngay|tuần|tuan|tháng|thang)\b", q)) and not capabilities.get("has_daily_stock_history"))
    )
    return not hard, hard


def check_candidate(
    candidate: Candidate,
    question: str = "",
    capabilities: dict[str, Any] | None = None,
    periods: dict[str, Any] | None = None,
) -> CheckReport:
    """Check result shape and common numerical/entity invariants."""
    flags = list(dict.fromkeys(candidate.flags))
    capabilities = capabilities or {}
    supported, _ = _intent_support(question, capabilities)
    if not supported:
        flags.append("UNSUPPORTED_DATA")
    rows = candidate.rows or []
    if candidate.answer_type != "unanswerable":
        if not rows or all(value is None for row in rows for value in row):
            flags.append("EMPTY")
        expected = {
            "number": len(rows) == 1 and len(rows[0]) == 1,
            "percent": len(rows) == 1 and len(rows[0]) == 1,
            "string": len(rows) == 1 and len(rows[0]) == 1,
            "date": len(rows) == 1 and len(rows[0]) == 1,
            "boolean": len(rows) == 1 and len(rows[0]) == 1,
            "list": bool(rows) and all(len(row) == 1 for row in rows),
            "table": bool(rows) and bool(candidate.columns),
        }.get(candidate.answer_type, False)
        if not expected:
            flags.append("SHAPE")
        if candidate.answer_type == "percent" and rows and rows[0][0] is not None:
            try:
                value = float(rows[0][0])
                if not -100 <= value <= 1000:
                    flags.append("PCT_RANGE")
            except (TypeError, ValueError):
                flags.append("SHAPE")
        if candidate.answer_type == "string" and capabilities.get("entity_values"):
            values = {_norm(value) for value in capabilities["entity_values"]}
            if _norm(rows[0][0]) not in values:
                flags.append("UNKNOWN_ENTITY")
    return CheckReport(tuple(dict.fromkeys(flags)), supported)


def check_candidates(
    candidates: Iterable[Candidate],
    question: str = "",
    capabilities: dict[str, Any] | None = None,
    periods: dict[str, Any] | None = None,
) -> list[CheckReport]:
    reports = []
    for candidate in candidates:
        report = check_candidate(candidate, question, capabilities, periods)
        candidate.flags.extend(flag for flag in report.flags if flag not in candidate.flags)
        candidate.canon = repr(canonical(candidate.columns, candidate.rows, candidate.answer_type))
        reports.append(report)
    return reports


def decide(
    candidates: Iterable[Candidate],
    capabilities: dict[str, Any] | None = None,
    tau: float = 0.6,
    question: str = "",
    periods: dict[str, Any] | None = None,
) -> tuple[str, Candidate | None]:
    """Return an answer only for one executed, non-null, type-compatible result."""
    candidates = list(candidates)
    if not candidates:
        return "abstain", None
    candidate = candidates[0]
    if candidate.answer_type == "unanswerable":
        return "unanswerable", None
    rows = candidate.rows or []
    if candidate.error or not rows or all(value is None for row in rows for value in row):
        return "abstain", None
    if candidate.answer_type in {"number", "percent"}:
        try:
            float(rows[0][0])
        except (TypeError, ValueError):
            return "abstain", None
    elif candidate.answer_type == "boolean" and not isinstance(rows[0][0], bool):
        return "abstain", None
    elif candidate.answer_type in {"list", "table"}:
        if not all(row for row in rows):
            return "abstain", None
        if candidate.answer_type == "list" and not all(len(row) == 1 for row in rows):
            return "abstain", None
        if candidate.answer_type == "table" and not candidate.columns:
            return "abstain", None
    elif candidate.answer_type in {"string", "date"} and rows[0][0] is None:
        return "abstain", None
    return "answer", candidate


__all__ = ["CheckReport", "canonical", "check_candidate", "check_candidates", "decide"]
