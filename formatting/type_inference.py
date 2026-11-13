"""Question-only answer type inference."""

from __future__ import annotations

import re
import unicodedata


def _normalize(question: str) -> str:
    text = unicodedata.normalize("NFD", str(question).lower().replace("đ", "d"))
    return " ".join("".join(c for c in text if unicodedata.category(c) != "Mn").split())


def infer_question_type(question: str) -> tuple[str | None, float]:
    """Return ``(type, confidence)`` using generic Vietnamese language cues."""
    text = _normalize(question)
    if not text:
        return None, 0.0
    rules = (
        ("percent", r"(?:%|phan tram|ti le|ty le|bao nhieu\s*%)"),
        ("date", r"\b(?:ngay nao|hom nao|thang nao)\b"),
        ("boolean", r"\b(?:co phai|dung khong|phai khong|co .* khong)\b"),
        ("list", r"\b(?:top\s*\d+|liet ke|nhung .* nao|danh sach)\b"),
        ("table", r"\b(?:so sanh|bang)\b"),
        ("string", r"\b(?:ai|hang nao|san pham nao|khach nao|nao .* nhat)\b"),
        ("number", r"\b(?:bao nhieu|may|tong|trung binh|so)\b"),
    )
    for answer_type, pattern in rules:
        if re.search(pattern, text):
            return answer_type, 0.9
    return None, 0.0


def expected_type(question: str) -> str | None:
    answer_type, confidence = infer_question_type(question)
    return answer_type if confidence >= 0.6 else None


__all__ = ["expected_type", "infer_question_type"]
