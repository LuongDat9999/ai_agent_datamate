"""Audit date columns exposed by each loaded business context."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import gc
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dataprocess.loader import load_business


DATE_NAME_TOKENS = ("ngay", "date", "thoi_gian")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def audit_business(business_dir: Path) -> tuple[str, list[dict[str, object]], str | None]:
    context = None
    rows: list[dict[str, object]] = []
    error = None
    try:
        context = load_business(business_dir, business_dir.name)
        relations = context.con.execute(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        for name, relation_type in relations:
            quoted = _quote_identifier(name)
            columns = context.con.execute(f"DESCRIBE {quoted}").fetchall()
            column_info = [(str(column[0]), str(column[1])) for column in columns]
            date_named = [
                f"{column} ({dtype})"
                for column, dtype in column_info
                if any(token in column.casefold() for token in DATE_NAME_TOKENS)
            ]
            ngay = next(((column, dtype) for column, dtype in column_info if column == "ngay"), None)
            row_count = context.con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            rows.append(
                {
                    "name": name,
                    "type": relation_type,
                    "rows": row_count,
                    "ngay": ngay,
                    "date_named": date_named,
                    "has_date_candidate": bool(date_named),
                }
            )
    except Exception as caught:
        error = f"{type(caught).__name__}: {caught}"
    finally:
        del context
        gc.collect()
    return business_dir.name, rows, error


def render_report(results: list[tuple[str, list[dict[str, object]]]]) -> str:
    lines = ["# Audit cột `ngay`", ""]
    all_rows = [row for _, rows, _ in results for row in rows]
    has_ngay = sum(row["ngay"] is not None for row in all_rows)
    missing_with_candidate = sum(row["ngay"] is None and row["has_date_candidate"] for row in all_rows)
    missing_without_date = sum(row["ngay"] is None and not row["has_date_candidate"] for row in all_rows)
    lines.extend(
        [
            "## Tổng hợp",
            "",
            f"- Bảng/view có `ngay`: **{has_ngay}**",
            f"- Không có `ngay` nhưng có cột ngày ứng viên: **{missing_with_candidate}**",
            f"- Không có cột ngày nào: **{missing_without_date}**",
            "",
        ]
    )
    for business_id, rows, error in results:
        lines.extend([f"## {business_id}", "", "| Quan hệ | Loại | Dòng | `ngay` | Cột ngày ứng viên |", "|---|---|---:|---|---|"])
        if error:
            lines.extend([f"> Audit lỗi khi load business: `{error}`", ""])
        for row in rows:
            ngay = f"{row['ngay'][0]} ({row['ngay'][1]})" if row["ngay"] else "không có"
            candidates = ", ".join(row["date_named"]) or "không có"
            lines.append(f"| `{row['name']}` | {row['type']} | {row['rows']} | {ngay} | {candidates} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "audit_ngay.md")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if any(path.is_file() for path in args.data_root.iterdir()):
        business_dirs = [args.data_root]
    else:
        business_dirs = sorted(
            path for path in args.data_root.glob("*/data/*") if path.is_dir()
        )
    if not business_dirs and args.data_root.is_dir():
        business_dirs = sorted(path for path in args.data_root.iterdir() if path.is_dir())
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(audit_business, business_dirs))
    report = render_report(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
