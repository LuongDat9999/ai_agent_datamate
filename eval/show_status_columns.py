from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent_datamate.dataprocess.cleaner import vn_norm
from ai_agent_datamate.dataprocess.loader import load_business


STATUS_HINTS = (
    "huy",
    "tra",
    "giao",
    "xong",
    "hoan thanh",
    "hoan tat",
    "trang thai",
    "ket qua",
    "tinh trang",
)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _is_status_hint(value: object) -> bool:
    normalized = vn_norm(value)
    return any(hint in normalized for hint in STATUS_HINTS)


def show_status_columns(split_dir: Path, limit: int | None = None) -> str:
    data_dir = split_dir / "data"
    business_dirs = sorted(path for path in data_dir.iterdir() if path.is_dir())
    if limit is not None:
        business_dirs = business_dirs[: max(0, limit)]
    lines: list[str] = []
    for business_dir in business_dirs:
        business_id = business_dir.name
        context = load_business(business_dir, business_id)
        lines.append(f"BUSINESS {business_id}")
        found = False
        for table_name, profile in context.tables.items():
            for column in profile.columns:
                if column == "is_total":
                    continue
                quoted_table = _quote_identifier(table_name)
                quoted_column = _quote_identifier(column)
                rows = context.con.execute(
                    f"""
                    SELECT {quoted_column} AS value, COUNT(*) AS count
                    FROM {quoted_table}
                    WHERE {quoted_column} IS NOT NULL
                    GROUP BY {quoted_column}
                    ORDER BY count DESC, value
                    """
                ).fetchall()
                if not rows or len(rows) > 12:
                    continue
                if not (_is_status_hint(column) or any(_is_status_hint(value) for value, _ in rows)):
                    continue
                found = True
                lines.append(f"  TABLE {table_name} | COLUMN {column} | distinct={len(rows)}")
                for value, count in rows:
                    lines.append(f"    {value!r}: {count}")
        if not found:
            lines.append("  (không có cột phù hợp)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split_dir", default="data/train")
    parser.add_argument("--output", default="outputs/status_columns.txt")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    text = show_status_columns(Path(args.split_dir), args.limit)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
