"""Diagnose order-status totals and missing months for one business."""

from __future__ import annotations

import argparse
import gc
import itertools
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent_datamate.dataprocess import load_business, scan_business


ROOT = Path(__file__).resolve().parents[1]
TARGETS: dict[str, tuple[str, int]] = {
    "2025-07-21": ("doanh thu", 8_680_000),
    "2025-09-18": ("doanh thu", 8_134_000),
    "2025-10-30": ("số đơn", 59),
}
MISSING_MONTHS = ("2025-04", "2025-05", "2025-11", "2026-02", "2026-04")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _fmt(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _status_columns(context: Any, view: str) -> tuple[list[str], list[str]]:
    names = [str(row[0]) for row in context.con.execute(f"DESCRIBE {_quote(view)}").fetchall()]
    detected = {
        status.column
        for status in context.status_columns.values()
    }
    detected.update(
        name for name in names
        if name != "trang_thai_chuan"
        and name != "trang_thai_nhom"
        and any(token in name.casefold() for token in ("trang_thai", "status", "da_huy", "da_hoan"))
    )
    detected.discard("trang_thai_nhom")
    return sorted(detected), sorted(name for name in detected if name in names)


def _status_rows(context: Any, view: str, day: str, raw_columns: list[str]) -> list[tuple[Any, ...]]:
    group_columns = ["trang_thai_chuan", *raw_columns]
    grouped = ", ".join(_quote(column) for column in group_columns)
    return context.con.execute(
        f"""
        SELECT {grouped}, COUNT(DISTINCT ma_don_hang) AS so_don, SUM(tong_tien) AS doanh_thu
        FROM {_quote(view)}
        WHERE ngay = DATE '{day}'
        GROUP BY {grouped}
        ORDER BY trang_thai_chuan, {", ".join(_quote(column) for column in raw_columns)}
        """
    ).fetchall()


def _matching_combinations(
    rows: list[tuple[Any, ...]], raw_count: int, metric: str, gold: int
) -> list[str]:
    totals: dict[str, tuple[int, int]] = {}
    for row in rows:
        raw_values = ", ".join(_fmt(value) for value in row[1 : 1 + raw_count])
        status = f"{row[0]} [{raw_values}]"
        orders, revenue = int(row[1 + raw_count] or 0), int(row[2 + raw_count] or 0)
        old = totals.get(status, (0, 0))
        totals[status] = (old[0] + orders, old[1] + revenue)
    statuses = sorted(totals)
    matches = []
    for size in range(1, len(statuses) + 1):
        for selected in itertools.combinations(statuses, size):
            index = 0 if metric == "số đơn" else 1
            if sum(totals[status][index] for status in selected) == gold:
                matches.append(" + ".join(selected))
    return matches


def _render_status_section(context: Any, view: str) -> list[str]:
    all_status_columns, raw_columns = _status_columns(context, view)
    lines = [
        "## A. Trạng thái",
        "",
        f"- Cột trạng thái gốc: "
        f"{', '.join(f'`{name}`' for name in all_status_columns) or 'không tìm thấy'}",
        f"- Cột trạng thái dùng để đối chiếu trong view: "
        f"{', '.join(f'`{name}`' for name in raw_columns) or 'không tìm thấy'}",
        "- Cột chuẩn: `trang_thai_chuan`",
        "",
    ]
    for day, (metric, gold) in TARGETS.items():
        rows = _status_rows(context, view, day, raw_columns)
        lines.extend([
            f"### {day} — gold {metric}: `{gold}`",
            "",
            "| trang_thai_chuan | Giá trị gốc tương ứng | Số đơn | SUM(tong_tien) |",
            "|---|---|---:|---:|",
        ])
        for row in rows:
            standardized, raw_values = row[0], row[1 : 1 + len(raw_columns)]
            lines.append(
                f"| `{standardized}` | "
                f"{', '.join(f'`{_fmt(value)}`' for value in raw_values)} | "
                f"{_fmt(row[1 + len(raw_columns)])} | {_fmt(row[2 + len(raw_columns)])} |"
            )
        matches = _matching_combinations(rows, len(raw_columns), metric, gold)
        lines.extend([
            "",
            f"- Tổ hợp trạng thái khớp gold ({metric}): "
            + (", ".join(f"`{match}`" for match in matches) if matches else "không có"),
            "",
        ])
    return lines


def _render_month_section(context: Any, business_dir: Path, view: str) -> list[str]:
    counts = context.con.execute(
        f"""
        SELECT strftime(ngay, '%Y-%m') AS thang, COUNT(*) AS so_dong
        FROM {_quote(view)}
        WHERE ngay IS NOT NULL
        GROUP BY thang
        ORDER BY thang
        """
    ).fetchall()
    provenance = context.con.execute(
        f"""
        SELECT DISTINCT strftime(ngay, '%Y-%m') AS thang, _nguon_file, _nguon_sheet
        FROM {_quote(view)}
        WHERE ngay IS NOT NULL
        """
    ).fetchall()
    source_by_month = {
        month: f"{file_name} / {sheet}"
        for month, file_name, sheet in provenance
    }
    source_files = [path.name for path in scan_business(business_dir).data]
    lines = [
        "## B. Tháng thiếu",
        "",
        "### Số dòng theo tháng trong `donhang_chitiet`",
        "",
        "| Tháng | Số dòng |",
        "|---|---:|",
    ]
    lines.extend(f"| `{month}` | {count} |" for month, count in counts)
    lines.extend([
        "",
        "### File gốc",
        "",
        "- " + ", ".join(f"`{name}`" for name in source_files),
        "",
        "Tháng được đánh dấu có file gốc khi có provenance file/sheet tương ứng.",
        "",
        "| Tháng | File/sheet gốc | Có dòng trong view |",
        "|---|---|---|",
    ])
    present_months = {month for month, _ in counts}
    for month in MISSING_MONTHS:
        lines.append(
            f"| `{month}` | `{source_by_month.get(month, 'không có')}` | "
            f"{'có' if month in present_months else 'không'} |"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--business", default="biz_102")
    parser.add_argument("--split_dir", default="data/train")
    parser.add_argument("--out", default="outputs/diag_status.md")
    args = parser.parse_args()

    split_dir = Path(args.split_dir)
    if not split_dir.is_absolute():
        split_dir = ROOT / split_dir
    business_dir = split_dir / "data" / args.business
    context = None
    try:
        context = load_business(business_dir, args.business)
        view = "donhang_chitiet"
        if view not in context.tables:
            raise RuntimeError(f"Không tìm thấy view `{view}`")
        report = [
            f"# Chẩn đoán trạng thái — `{args.business}`",
            "",
            *_render_status_section(context, view),
            *_render_month_section(context, business_dir, view),
        ]
    finally:
        if context is not None:
            context.con.close()
            del context
        gc.collect()

    output = Path(args.out)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(report) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
