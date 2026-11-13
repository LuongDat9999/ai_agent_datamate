"""Compare resolver metric accuracy with and without exact-row deduplication."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent_datamate.dataprocess import load_business
from ai_agent_datamate.tool.date_resolver import resolve_time
from ai_agent_datamate.tool.metrics import build_metric_sql, infer_metric_config_for_context
from ai_agent_datamate.tool.qtype import classify_question, is_simple_metric_question


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split_dir", default="data/train")
    parser.add_argument("--business", action="append")
    args = parser.parse_args()
    root = Path(args.split_dir)
    questions = _read_jsonl(root / "questions.jsonl")
    answers = {str(item["id"]): item.get("answer") for item in _read_jsonl(root / "answers.jsonl")}
    selected = set(args.business or [])
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in questions:
        business = str(item.get("business_id", ""))
        if (not selected or business in selected) and str(item["id"]) in answers:
            grouped[business].append(item)

    rulebook = (root / "rulebook.md").read_text(encoding="utf-8") if (root / "rulebook.md").exists() else ""
    for business in sorted(grouped):
        context = load_business(root / "data" / business, business)
        config = infer_metric_config_for_context(context)
        if not config.get("ok"):
            print(business, "infer_metric_config=False", config.get("reason"))
            continue
        scores = {False: 0, True: 0}
        total = 0
        for item in grouped[business]:
            question = str(item.get("question", ""))
            allowed, _ = is_simple_metric_question(question, conn=context.con, business_id=business)
            archetype = classify_question(question).get("archetype")
            if not allowed or archetype not in {"revenue_period", "order_count_period"}:
                continue
            periods = resolve_time(question, item["as_of_date"], rulebook=rulebook)["periods"]
            period = next(iter(periods.values()), None)
            if not period:
                continue
            metric = "revenue" if archetype == "revenue_period" else "orders"
            total += 1
            for dedupe in (False, True):
                trial = dict(config, dedupe=dedupe)
                value = context.con.execute(
                    build_metric_sql(trial, metric, period["start"], period["end"])
                ).fetchone()[0]
                if value == answers[str(item["id"])]:
                    scores[dedupe] += 1
        config["dedupe"] = scores[True] > scores[False] and scores[True] >= scores[False]
        print(
            business,
            f"questions={total}",
            f"without_dedupe={scores[False]}",
            f"with_dedupe={scores[True]}",
            f"config.dedupe={config['dedupe']}",
            f"revenue_column={config.get('revenue_column')}",
            f"reason={config.get('revenue_column_reason')}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
