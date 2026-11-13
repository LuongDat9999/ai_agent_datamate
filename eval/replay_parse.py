"""Replay parser and question-type inference without LLM or SQL execution."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from ai_agent_datamate.llm.generator import _candidate_from_response, resolve_candidate_type


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("debug", nargs="?", default="outputs/debug/debug.jsonl")
    parser.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args()
    records = [json.loads(line) for line in Path(args.debug).read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = [
        (record, candidate)
        for record in records
        for candidate in record.get("candidates", [])
    ]
    before = sum(candidate.get("abstain_reason") == "invalid_answer_type" for _, candidate in entries)
    replayed = []
    for record, debug_candidate in entries:
        question = str(record.get("question", ""))
        candidate = _candidate_from_response(str(debug_candidate.get("raw_response", "")))
        resolve_candidate_type(candidate, question)
        replayed.append((record, candidate))
    after = sum(
        bool(candidate.sql) and candidate.answer_type in {
            "number", "percent", "string", "date", "boolean", "list", "table", "unanswerable"
        }
        for _, candidate in replayed
    )
    abstains = Counter(
        candidate.abstain_reason or (
            "type_undetermined" if candidate.answer_type == "undetermined" else "none"
        )
        for _, candidate in replayed
        if not (candidate.sql and candidate.answer_type in {
            "number", "percent", "string", "date", "boolean", "list", "table", "unanswerable"
        })
    )
    types = Counter(candidate.inferred_type for _, candidate in replayed if candidate.inferred_type)
    print(f"before invalid_answer_type: {before}")
    print(f"after parseable SQL + resolved type: {after}")
    print(f"still abstain: {sum(abstains.values())}")
    print("abstain reasons:", dict(abstains))
    print("inferred types:", dict(types))
    print("\nquestion | inferred | declared | SQL")
    sample = replayed[:]
    random.Random(args.seed).shuffle(sample)
    for record, candidate in sample[:10]:
        print(f"{record.get('question', '')} | {candidate.inferred_type or '-'} | "
              f"{candidate.declared_type or '-'} | {candidate.sql or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
