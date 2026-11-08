"""Vietnamese query normalization and money annotations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

try:
    from ..dataprocess.cleaner import parse_money, vn_norm
except (ImportError, ModuleNotFoundError):
    import unicodedata

    def vn_norm(value: Any) -> str:
        text = unicodedata.normalize("NFC", str(value)).lower().replace("đ", "d")
        text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()

    def parse_money(value: Any) -> float | None:
        text = str(value).strip().lower().replace(",", ".")
        match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(k|tr|triệu|trieu|tỷ|ty)?", text)
        if not match:
            return None
        multipliers = {"k": 1e3, "tr": 1e6, "triệu": 1e6, "trieu": 1e6, "tỷ": 1e9, "ty": 1e9}
        return float(match.group(1)) * multipliers.get(match.group(2), 1)


ABBREVIATIONS = {
    "sp": "sản phẩm",
    "sx": "sản xuất",
    "kh": "khách hàng",
    "sl": "số lượng",
    "dc": "được",
    "ko": "không",
    "k": "không",
    "tk": "tồn kho",
}

# Deliberately excludes ambiguous tokens such as "dt".
_MONEY_RE = re.compile(
    r"(?<![\w])(?:\d+(?:[.,]\d+)?|\d+[.,]?\d*)\s*"
    r"(?:tỷ|ty|triệu|trieu|tr|nghìn|nghin|ngàn|ngan|k)"
    r"(?:\s*\d+)?(?![\w])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MoneyAnnotation:
    text: str
    value: float
    start: int
    end: int


@dataclass(frozen=True)
class AbbreviationResult:
    original: str
    normalized: str
    annotations: tuple[MoneyAnnotation, ...] = ()
    replacements: tuple[tuple[str, str], ...] = ()


def expand_abbreviations(question: str, mapping: dict[str, str] | None = None) -> str:
    """Expand unambiguous Vietnamese abbreviations without changing punctuation."""
    if not isinstance(question, str):
        raise TypeError("question must be a string")
    words = mapping or ABBREVIATIONS
    pattern = re.compile(r"(?<![\w])(" + "|".join(map(re.escape, sorted(words, key=len, reverse=True))) + r")(?![\w])", re.I)
    return pattern.sub(lambda match: words.get(match.group(1).lower(), match.group(1)), question)


def find_money_annotations(question: str) -> list[MoneyAnnotation]:
    """Extract money expressions while preserving their original offsets."""
    result: list[MoneyAnnotation] = []
    for match in _MONEY_RE.finditer(question):
        value = parse_money(match.group(0))
        if value is not None:
            result.append(MoneyAnnotation(match.group(0), value, match.start(), match.end()))
    return result


def normalize_question(question: str, mapping: dict[str, str] | None = None) -> AbbreviationResult:
    expanded = expand_abbreviations(question, mapping)
    return AbbreviationResult(
        original=question,
        normalized=expanded,
        annotations=tuple(find_money_annotations(expanded)),
        replacements=tuple((key, value) for key, value in (mapping or ABBREVIATIONS).items()),
    )


def expand_abbrev(question: str, mapping: dict[str, str] | None = None) -> dict[str, Any]:
    """Dictionary-shaped adapter used by the question-context pipeline."""
    result = normalize_question(question, mapping)
    return {
        "original_question": result.original,
        "normalized_question": result.normalized,
        "annotations": [
            {"text": item.text, "value": item.value, "start": item.start, "end": item.end}
            for item in result.annotations
        ],
    }


__all__ = [
    "ABBREVIATIONS",
    "AbbreviationResult",
    "MoneyAnnotation",
    "expand_abbrev",
    "expand_abbreviations",
    "find_money_annotations",
    "normalize_question",
]