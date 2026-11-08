from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent_datamate.dataprocess.loader import load_business
from ai_agent_datamate.tool.fewshot import FewShotRetriever, load_bank, retrieve_fewshots
from ai_agent_datamate.tool.prompts import build_prompt, _estimate_tokens


def _sample_evenly(items: list[dict], limit: int) -> list[dict]:
    if limit <= 0 or len(items) <= limit:
        return items
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(str(item.get("business_id", "")), []).append(item)
    selected = []
    while len(selected) < limit:
        progressed = False
        for business_id in sorted(grouped):
            if grouped[business_id]:
                selected.append(grouped[business_id].pop(round((len(grouped[business_id]) - 1) / 2)))
                progressed = True
                if len(selected) >= limit:
                    break
        if not progressed:
            break
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split_dir", required=True)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    split = Path(args.split_dir)
    questions = [json.loads(line) for line in (split / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    questions.sort(key=lambda item: (str(item.get("business_id", "")), str(item.get("id", ""))))
    selected = _sample_evenly(questions, args.limit)
    rulebook = (split / "rulebook.md").read_text(encoding="utf-8") if (split / "rulebook.md").exists() else ""
    bank = FewShotRetriever(load_bank(split / "fewshot_bank.jsonl")) if (split / "fewshot_bank.jsonl").exists() else None
    cache = {}
    totals = {"rulebook": 0, "readme": 0, "schema": 0, "few-shot": 0, "entity hints": 0, "question": 0, "total": 0}
    for item in selected:
        business_id = str(item.get("business_id", ""))
        cache.setdefault(business_id, load_business(split / "data" / business_id, business_id))
        context = cache[business_id]
        fewshots = retrieve_fewshots(item["question"], bank, exclude_business_id=business_id) if bank else []
        qctx, prompt = build_prompt(item["question"], item["as_of_date"], context, rulebook=rulebook, fewshots=fewshots)
        parts = {
            "rulebook": rulebook,
            "readme": str(context.readme_text),
            "schema": str(context.schema_prompt),
            "few-shot": "\n".join(str(x) for x in fewshots),
            "entity hints": "\n".join(str(x) for x in qctx.entity_hints),
            "question": item["question"],
        }
        counts = {name: _estimate_tokens(text) for name, text in parts.items()}
        counts["total"] = _estimate_tokens(prompt)
        for name, value in counts.items():
            totals[name] += value
        largest = max((name for name in counts if name != "total"), key=counts.get)
        print(json.dumps({"id": item.get("id"), **counts, "largest": largest}, ensure_ascii=False))
    if selected:
        averages = {name: round(value / len(selected), 2) for name, value in totals.items()}
        largest = max((name for name in averages if name != "total"), key=averages.get)
        print(json.dumps({"average": averages, "largest": largest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
