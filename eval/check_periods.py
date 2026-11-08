"""Audit decoded time periods across the complete train question set."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent_datamate.tool.date_resolver import resolve_time


ROOT = Path(__file__).resolve().parents[1]
SPLIT = ROOT / "data" / "train"


def _questions() -> list[dict]:
    path = SPLIT / "questions.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _show(title: str, rows: list[tuple[str, str, str]]) -> None:
    print(f"{title}: {len(rows)}")
    for qid, question, detail in rows[:20]:
        print(f"  {qid} | {question} | {detail}")


def main() -> int:
    rulebook_path = SPLIT / "rulebook.md"
    rulebook = rulebook_path.read_text(encoding="utf-8") if rulebook_path.exists() else ""
    calendar_path = SPLIT / "calendar.csv"
    calendar = pd.read_csv(calendar_path) if calendar_path.exists() else None
    empty: list[tuple[str, str, str]] = []
    wrong_year: list[tuple[str, str, str]] = []
    future: list[tuple[str, str, str]] = []
    time_word = re.compile(
        r"\b(hôm|hom|ngày|ngay|tuần|tuan|tháng|thang|thg|quý|quy|q[1-4]|"
        r"năm|nam|đầu|dau|tết|tet|dịp|dip)\b",
        re.I,
    )
    explicit_year = re.compile(r"\b20\d{2}\b")

    for question in _questions():
        resolved = resolve_time(question["question"], question["as_of_date"], calendar=calendar, rulebook=rulebook)
        periods = resolved["periods"]
        text = question["question"]
        if time_word.search(text) and not periods:
            empty.append((question["id"], text, "periods={}"))
        years = {int(value) for value in explicit_year.findall(text)}
        if years and periods:
            bad = [
                f"{name}={value.get('start')}..{value.get('end')}"
                for name, value in periods.items()
                if name in {"month", "quarter", "year"}
                if int(str(value["start"])[:4]) not in years or int(str(value["end"])[:4]) not in years
            ]
            if bad:
                wrong_year.append((question["id"], text, "; ".join(bad)))
        if "FUTURE_PERIOD" in resolved["warnings"]:
            future.append((question["id"], text, str(periods)))

    _show("(a) time words but empty periods", empty)
    _show("(b) explicit year but decoded period outside it", wrong_year)
    _show("(c) FUTURE_PERIOD", future)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
