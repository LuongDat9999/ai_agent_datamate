"""Audit entity hints over all training questions."""

from __future__ import annotations

import argparse
import json
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent_datamate.dataprocess.loader import load_business
from ai_agent_datamate.tool.entity_matcher import match_entities


def _audit_business(args: tuple[Path, str, list[dict]]) -> list[tuple[str, str]]:
    split, business_id, questions = args
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        context = load_business(split / "data" / business_id, business_id)
    return [
        (question["id"], question["question"])
        for question in questions
        if not match_entities(question["question"], context.entity_index)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split_dir", default="ai_agent_datamate/data/train")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    split = Path(args.split_dir)
    questions = [
        json.loads(line)
        for line in (split / "questions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    grouped: dict[str, list[dict]] = {}
    for question in questions:
        grouped.setdefault(str(question.get("business_id", "")), []).append(question)
    jobs = [(split, business_id, items) for business_id, items in grouped.items()]
    empty = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(_audit_business, job) for job in jobs]
        for future in as_completed(futures):
            empty.extend(future.result())
    empty.sort(key=lambda item: item[0])
    print(f"entity_hints rỗng: {len(empty)} / {len(questions)}")
    print("20 ví dụ:")
    for qid, text in empty[:20]:
        print(f"  {qid} | {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
