"""Deterministic fuzzy matching of Vietnamese entities."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

try:
    from rapidfuzz import fuzz, process
except ImportError:
    fuzz = process = None

try:
    from ..dataprocess.cleaner import vn_norm
except (ImportError, ModuleNotFoundError):
    import unicodedata

    def vn_norm(value: Any) -> str:
        text = unicodedata.normalize("NFC", str(value)).lower().replace("đ", "d")
        text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
        return " ".join("".join(c if c.isalnum() else " " for c in text).split())


STOPWORDS = {
    "san", "pham", "san pham", "tong", "thang", "nam", "hang", "so", "cho", "hom",
    "qua", "quy", "thang", "tuan", "ngay", "bao", "nhieu", "don", "la", "vay",
    "mat", "thu", "ve", "tien", "bao nhieu", "hôm", "hôm qua", "tháng", "năm",
    "hàng", "sản phẩm", "tổng",
}
_TIME_OR_NUMBER = STOPWORDS | {"quy", "q", "1", "2", "3", "4"}


@dataclass(frozen=True)
class EntityHint:
    query: str
    table: str
    column: str
    value: str
    score: float
    alternatives: tuple[str, ...] = ()


def _score(query: str, value: str) -> float:
    if fuzz is not None:
        return float(fuzz.WRatio(query, value))
    return SequenceMatcher(None, query, value).ratio() * 100


def _columns(index: Any) -> Iterable[tuple[str, str, list[str]]]:
    values = getattr(index, "values", None)
    if values is None or callable(values):
        values = index
    for column, tables in (values or {}).items():
        normalized_column = vn_norm(column)
        if normalized_column.endswith("_raw") or normalized_column.endswith("_goc"):
            continue
        if any(token in normalized_column for token in ("ngay", "thoi gian", "date", "time", "thang", "nam")):
            continue
        if any(token in normalized_column for token in ("so luong", "gia", "tien", "amount", "number", "id")):
            continue
        if isinstance(tables, dict):
            for table, entries in tables.items():
                yield str(table), str(column), [str(item) for item in entries]


def _spans(question: str, max_words: int = 7) -> set[str]:
    tokens = vn_norm(question).split()
    spans = set()
    for start in range(len(tokens)):
        for end in range(start + 1, min(len(tokens), start + max_words) + 1):
            span_tokens = tokens[start:end]
            span = " ".join(span_tokens)
            if len(span_tokens) == 1:
                if len(span) < 5 or span in _TIME_OR_NUMBER:
                    continue
            elif any(token in _TIME_OR_NUMBER or token.isdigit() for token in span_tokens):
                continue
            if span not in STOPWORDS:
                spans.add(span)
    return spans


def match_entities(question: str, index: Any, topk: int = 3, threshold: float = 85, limit: int = 5) -> list[EntityHint]:
    candidates: list[EntityHint] = []
    for query in _spans(question):
        for table, column, values in _columns(index):
            if not values:
                continue
            matches = (
                process.extract(query, [vn_norm(value) for value in values], scorer=fuzz.WRatio, limit=topk, score_cutoff=threshold)
                if process is not None
                else sorted(((vn_norm(value), _score(query, vn_norm(value)), idx) for idx, value in enumerate(values)), key=lambda item: item[1], reverse=True)[:topk]
            )
            for _, score, idx in matches:
                minimum = 95 if len(query.split()) == 1 else 90
                if score < minimum:
                    continue
                candidates.append(EntityHint(query, table, column, values[idx], round(float(score), 2)))
    candidates.sort(key=lambda item: (len(item.query), item.score), reverse=True)
    unique: list[EntityHint] = []
    seen: set[tuple[str, str, str]] = set()
    seen_values: set[str] = set()
    for hint in candidates:
        key = (hint.table, hint.column, vn_norm(hint.value))
        if key not in seen and vn_norm(hint.value) not in seen_values:
            seen.add(key)
            seen_values.add(vn_norm(hint.value))
            unique.append(hint)
        if len(unique) >= limit:
            break
    return unique


def entity_hints(question: str, index: Any, topk: int = 3, thr: float = 85) -> list[EntityHint]:
    return match_entities(question, index, topk=topk, threshold=thr)


def format_entity_hints(hints: Iterable[EntityHint]) -> str:
    return "\n".join(
        f'- "{hint.query}" → {hint.table}.{hint.column} = "{hint.value}" ({hint.score:g})'
        for hint in hints
    ) or "(không phát hiện)"


__all__ = ["EntityHint", "entity_hints", "format_entity_hints", "match_entities"]