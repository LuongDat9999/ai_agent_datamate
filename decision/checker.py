"""Result sanity checks and self-consistency voting for DataMate."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from os import environ
from typing import Any, Iterable

from ..llm.generator import ANSWER_TYPES, Candidate, resolve_candidate_type
from ..sqlrunner.validator import HARD_FLAGS

SOFT_FLAGS = frozenset({"UNIT_MISMATCH"})
HARD_DECISION_FLAGS = HARD_FLAGS | frozenset({
    "PARSE_ERROR", "FANOUT_RISK", "SHAPE", "PCT_RANGE", "UNKNOWN_ENTITY",
    "JOIN_MISMATCH", "EMPTY", "UNSUPPORTED_DATA", "NO_STATUS_FILTER",
    "LINE_GRAIN_ORDER_METRIC", "NO_DATE_FILTER",
})
HEAVY_FLAGS = HARD_DECISION_FLAGS


@dataclass(frozen=True)
class DecisionConfig:
    min_confidence: float = 0.6
    soft_flag_penalty: float = 0.1
    retry_penalty: float = 0.05
    result_size_penalty: float = 0.1

    @classmethod
    def from_env(cls) -> "DecisionConfig":
        def number(name: str, default: float) -> float:
            try:
                value = float(environ.get(name, default))
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be numeric")
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
            return value

        return cls(
            min_confidence=number("DECISION_MIN_CONFIDENCE", cls.min_confidence),
            soft_flag_penalty=number("DECISION_SOFT_FLAG_PENALTY", cls.soft_flag_penalty),
            retry_penalty=number("DECISION_RETRY_PENALTY", cls.retry_penalty),
            result_size_penalty=number("DECISION_RESULT_SIZE_PENALTY", cls.result_size_penalty),
        )


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


def _set_reason(candidate: Candidate, reason: str) -> None:
    candidate.abstain_reason = reason


def _sql_complete(sql: str | None) -> bool:
    if not isinstance(sql, str) or not sql.strip():
        return False
    text = sql.strip().rstrip(";").strip()
    if text.count("(") != text.count(")") or text.count("'") % 2 or text.count('"') % 2:
        return False
    return not re.search(r"(?:\bfrom|\bjoin|\bwhere|\band|\bor|,|=)\s*$", text, re.IGNORECASE)


def _blocking_flags(candidate: Candidate, periods: dict[str, Any] | None) -> set[str]:
    if "RESOLVER" in candidate.flags:
        return set()
    flags = set(candidate.flags) & HARD_DECISION_FLAGS
    return flags


def blocking_flags(candidate: Candidate, periods: dict[str, Any] | None = None) -> set[str]:
    """Return decision-gate flags, excluding resolver candidates."""
    return _blocking_flags(candidate, periods)


def confidence_for_candidates(
    candidates: Iterable[Candidate],
    winner: Candidate | None,
    *,
    config: DecisionConfig | None = None,
) -> float:
    config = config or DecisionConfig.from_env()
    candidates = list(candidates)
    if winner is None or not candidates:
        return 0.0
    eligible = [candidate for candidate in candidates if not _blocking_flags(candidate, None)]
    agreement = sum(candidate.canon == winner.canon for candidate in eligible) / len(candidates)
    confidence = agreement
    confidence -= config.soft_flag_penalty * len(set(winner.flags) & SOFT_FLAGS)
    confidence -= config.retry_penalty * int(getattr(winner, "_retry_count", 0))
    if len(winner.rows or []) > 1000:
        confidence -= config.result_size_penalty
    if not winner.rows or all(value is None for row in winner.rows for value in row):
        return 0.0
    return max(0.0, min(1.0, confidence))


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
    tau: float | None = None,
    question: str = "",
    periods: dict[str, Any] | None = None,
) -> tuple[str, Candidate | None]:
    """Return an answer only for a complete, safe, sufficiently confident result."""
    candidates = list(candidates)
    if not candidates:
        return "abstain", None
    config = DecisionConfig.from_env()
    threshold = config.min_confidence if tau is None else tau
    if not 0 <= threshold <= 1:
        raise ValueError("tau must be between 0 and 1")
    valid: list[Candidate] = []
    for candidate in candidates:
        if question:
            resolve_candidate_type(candidate, question)
        prior_reason = candidate.abstain_reason
        candidate.abstain_reason = None
        report = check_candidate(candidate, question, capabilities, periods)
        candidate.flags.extend(flag for flag in report.flags if flag not in candidate.flags)
        if prior_reason == "no_sql_found":
            _set_reason(candidate, prior_reason)
        elif candidate.answer_type == "undetermined":
            _set_reason(candidate, "type_undetermined")
        elif candidate.answer_type not in ANSWER_TYPES:
            _set_reason(candidate, "invalid_answer_type")
        elif candidate.answer_type != "unanswerable" and not _sql_complete(candidate.sql):
            _set_reason(candidate, "missing_or_incomplete_sql")
            candidate.flags.append("PARSE_ERROR")
        elif candidate.error:
            _set_reason(candidate, "execution_or_validation_error")
        elif _blocking_flags(candidate, periods):
            _set_reason(candidate, "hard_blocking_flags:" + ",".join(sorted(_blocking_flags(candidate, periods))))
        elif candidate.answer_type != "unanswerable" and not candidate.rows:
            _set_reason(candidate, "empty_result")
            candidate.flags.append("EMPTY")
        else:
            valid.append(candidate)
    if not valid:
        return "abstain", None
    answer_candidates = [candidate for candidate in valid if candidate.answer_type != "unanswerable"]
    if not answer_candidates:
        return "unanswerable", None
    for candidate in answer_candidates:
        candidate.canon = repr(canonical(candidate.columns, candidate.rows, candidate.answer_type))
    counts = {}
    for candidate in answer_candidates:
        counts[candidate.canon] = counts.get(candidate.canon, 0) + 1
    winner = max(answer_candidates, key=lambda item: counts[item.canon])
    confidence = confidence_for_candidates(answer_candidates, winner, config=config)
    if confidence < threshold:
        _set_reason(winner, f"confidence_below_threshold:{confidence:.3f}<{threshold:.3f}")
        return "abstain", None
    return "answer", winner


__all__ = [
    "CheckReport", "DecisionConfig", "HARD_DECISION_FLAGS", "HEAVY_FLAGS",
    "SOFT_FLAGS", "blocking_flags", "canonical", "check_candidate", "check_candidates",
    "confidence_for_candidates", "decide",
]
