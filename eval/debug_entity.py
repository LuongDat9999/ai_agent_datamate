"""Inspect entity indexing and fuzzy matching for one business/question."""

from __future__ import annotations

import argparse
from pathlib import Path

from ai_agent_datamate.dataprocess.loader import load_business
from ai_agent_datamate.tool.entity_matcher import (
    _columns,
    _score,
    _spans,
    match_entities,
    vn_norm,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split_dir", required=True)
    parser.add_argument("--business_id", required=True)
    parser.add_argument("--question", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    split = Path(args.split_dir)
    context = load_business(split / "data" / args.business_id, args.business_id)
    index = context.entity_index
    print("(a) EntityIndex columns")
    for table, column, values in _columns(index):
        print(f"  {table}.{column}: distinct={len(values)} samples={values[:5]}")

    spans = sorted(_spans(args.question), key=lambda value: (len(value.split()), value))
    print(f"(b) n-grams after vn_norm ({len(spans)}):")
    print("  " + " | ".join(spans))
    print("(c) top 3 per n-gram, threshold=85")
    entries = list(_columns(index))
    for query in spans:
        ranked = []
        for table, column, values in entries:
            for value in values:
                ranked.append((_score(vn_norm(query), vn_norm(value)), table, column, value))
        ranked.sort(reverse=True)
        print(f"  {query!r}: " + "; ".join(
            f"{table}.{column}={value!r} ({score:.2f})"
            for score, table, column, value in ranked[:3]
        ))

    print("(d) distinct data values containing 'vinataba' after vn_norm")
    found = set()
    for table in context.tables:
        for column in context.tables[table].columns:
            if column == "is_total":
                continue
            rows = context.con.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" '
                f'WHERE lower(CAST("{column}" AS VARCHAR)) LIKE \'%vinataba%\''
            ).fetchall()
            found.update((table, column, str(row[0])) for row in rows if row[0] is not None)
    for table, column, value in sorted(found):
        print(f"  {table}.{column}={value!r} (vn_norm={vn_norm(value)!r})")
    print("matched hints:")
    for hint in match_entities(args.question, index):
        print(f"  {hint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
