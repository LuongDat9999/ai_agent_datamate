from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return {str(item["id"]): item for line in handle if line.strip() for item in [json.loads(line)]}


def _norm_string(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value)).lower()
    return " ".join(re.sub(r"[^\wÀ-ỹĐđ]+", " ", text, flags=re.UNICODE).split())


def _equal(gold: Any, pred: Any, answer_type: str) -> bool:
    if answer_type in {"number", "percent"}:
        try:
            return math.isclose(round(float(gold), 2), round(float(pred), 2), abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    if answer_type == "string":
        return _norm_string(gold) == _norm_string(pred)
    if answer_type == "date":
        return str(gold).replace("/", "-") == str(pred).replace("/", "-")
    if answer_type == "boolean":
        return bool(gold) == bool(pred)
    if answer_type == "list":
        return isinstance(pred, list) and len(gold) == len(pred) and all(_equal(a, b, "string") for a, b in zip(gold, pred))
    if answer_type == "table":
        if not isinstance(gold, dict) or not isinstance(pred, dict) or gold.get("columns") != pred.get("columns"):
            return False
        left = {tuple(_norm_string(value) for value in row) for row in gold.get("rows", [])}
        right = {tuple(_norm_string(value) for value in row) for row in pred.get("rows", [])}
        return left == right
    return gold == pred


def score(pred_path: Path, gold_path: Path, all_gold: bool = False) -> int:
    pred, gold = _load(pred_path), _load(gold_path)
    question_path = gold_path.parent / "questions.jsonl"
    business_by_id = {qid: item.get("business_id", "") for qid, item in _load(question_path).items()} if question_path.exists() else {}
    by_type: dict[str, list[int]] = defaultdict(list)
    by_business: dict[str, list[int]] = defaultdict(list)
    counts = defaultdict(int)
    rows = []
    ids = list(gold) if all_gold else [qid for qid in pred if qid in gold]
    for qid in ids:
        expected = gold[qid]
        actual = pred.get(qid, {"type": "abstain"})
        gold_type, pred_type = expected.get("type"), actual.get("type")
        if pred_type == "abstain":
            result, points = "abstain", 0
            counts["abstain"] += 1
        else:
            correct = pred_type == gold_type and (
                gold_type == "unanswerable"
                or _equal(expected.get("answer"), actual.get("answer"), gold_type)
            )
            result, points = ("đúng", 1) if correct else ("sai", -1)
            counts["correct" if correct else "wrong"] += 1
            if gold_type == "unanswerable":
                counts["unanswerable"] += 1
        by_type[gold_type].append(points)
        by_business[str(business_by_id.get(qid, ""))].append(points)
        rows.append((qid, gold_type, pred_type, result, expected.get("answer"), actual.get("answer")))
    total = len(rows)
    print(f"đã chấm {total} câu / {len(gold)} câu gold")
    print(f"đúng={counts['correct']} sai={counts['wrong']} abstain={counts['abstain']} unanswerable={counts['unanswerable']}")
    print(f"PA={sum(sum(values) for values in by_type.values()) / total if total else 0:.6f}")
    print("PA theo type:")
    for key, values in sorted(by_type.items()):
        print(f"  {key}: {sum(values) / len(values):.6f}")
    print("PA theo business_id:")
    for key, values in sorted(by_business.items()):
        print(f"  {key}: {sum(values) / len(values):.6f}")
    print("id | type gold | type pred | kết quả | gold | pred")
    for row in rows:
        print(" | ".join(json.dumps(value, ensure_ascii=False) for value in row))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--all_gold", action="store_true", help="Chấm toàn bộ gold, kể cả id thiếu trong pred")
    args = parser.parse_args()
    raise SystemExit(score(Path(args.pred), Path(args.gold), all_gold=args.all_gold))