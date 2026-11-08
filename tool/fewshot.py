"""Retrieval of verified train examples without leaking test businesses."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from ..dataprocess.cleaner import vn_norm
except (ImportError, ModuleNotFoundError):
    import re
    import unicodedata

    def vn_norm(value: Any) -> str:
        text = unicodedata.normalize("NFC", str(value)).lower().replace("đ", "d")
        text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _tokens(text: str) -> list[str]:
    normalized = vn_norm(text)
    return [normalized[i : i + 4] for i in range(max(0, len(normalized) - 3))]


def _similarity(left: str, right: str) -> float:
    a, b = Counter(_tokens(left)), Counter(_tokens(right))
    if not a or not b:
        return 0.0
    common = sum((a & b).values())
    return common / math.sqrt(sum(a.values()) * sum(b.values()))


def load_bank(path: str | Path) -> list[dict[str, Any]]:
    entries = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f"few-shot line {line_number} is not an object")
            if item.get("question") and item.get("sql") and item.get("type"):
                entries.append(item)
    return entries


class FewShotRetriever:
    def __init__(self, examples: Iterable[Mapping[str, Any]] = ()) -> None:
        self.examples = [dict(item) for item in examples if item.get("question") and item.get("sql") and item.get("type")]

    def retrieve(
        self,
        question: str,
        top_k: int = 3,
        exclude_business_id: str | None = None,
        min_score: float = 0.08,
    ) -> list[dict[str, Any]]:
        scored = []
        for item in self.examples:
            if exclude_business_id is not None and item.get("business_id") == exclude_business_id:
                continue
            score = _similarity(question, str(item["question"]))
            if score >= min_score:
                result = dict(item)
                result["_score"] = round(score, 6)
                scored.append(result)
        scored.sort(key=lambda item: (-item["_score"], str(item.get("question", ""))))
        return scored[: max(0, top_k)]


def retrieve_fewshots(
    question: str,
    bank: Iterable[Mapping[str, Any]] | FewShotRetriever,
    top_k: int = 3,
    exclude_business_id: str | None = None,
    min_score: float = 0.08,
) -> list[dict[str, Any]]:
    retriever = bank if isinstance(bank, FewShotRetriever) else FewShotRetriever(bank)
    return retriever.retrieve(question, top_k, exclude_business_id, min_score)


__all__ = ["FewShotRetriever", "load_bank", "retrieve_fewshots"]
