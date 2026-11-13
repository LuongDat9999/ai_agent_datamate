"""Stream raw status columns from every training business into a compact report."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent_datamate.dataprocess.cleaner import vn_norm
from ai_agent_datamate.dataprocess.order_status import _classify_status


ROOT = Path(__file__).resolve().parents[1]
STATUS_HEADER_PARTS = ("trang thai", "hoan thanh", "status", "tinh trang")
SUSPICIOUS_PARTS = ("hoan", "tra", "huy", "cho", "dang", "chua")


def _is_status_header(value: Any) -> bool:
    normalized = vn_norm(value)
    return any(part in normalized for part in STATUS_HEADER_PARTS)


def _label(value: Any) -> str:
    return _classify_status(value) or "khong_ro"


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _xlsx_values(path: Path) -> Iterator[tuple[str, Counter[str]]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets[:3]:
            rows = sheet.iter_rows(values_only=True)
            try:
                header = next(rows)
            except StopIteration:
                continue
            indexes = [index for index, value in enumerate(header) if _is_status_header(value)]
            if not indexes:
                continue
            counters = [Counter() for _ in indexes]
            for row in rows:
                for counter, index in zip(counters, indexes):
                    if index < len(row):
                        value = _cell_text(row[index])
                        if value:
                            counter[value] += 1
            for index, counter in zip(indexes, counters):
                yield f"{sheet.title}:{header[index]}", counter
    finally:
        workbook.close()


def _csv_values(path: Path) -> Iterator[tuple[str, Counter[str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration:
            return
        indexes = [index for index, value in enumerate(header) if _is_status_header(value)]
        counters = [Counter() for _ in indexes]
        for row in reader:
            for counter, index in zip(counters, indexes):
                if index < len(row):
                    value = _cell_text(row[index])
                    if value:
                        counter[value] += 1
        for index, counter in zip(indexes, counters):
            yield f"{path.name}:{header[index]}", counter


def _file_values(path: Path) -> Iterator[tuple[str, Counter[str]]]:
    if path.suffix.casefold() == ".xlsx":
        yield from _xlsx_values(path)
    elif path.suffix.casefold() == ".csv":
        yield from _csv_values(path)


def _business_dirs(split_dir: Path) -> list[Path]:
    root = split_dir / "data"
    return sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []


def _scan_business(
    business_dir: Path,
    totals: Counter[tuple[str, str]],
    businesses: dict[tuple[str, str], set[str]],
) -> set[str]:
    unknown: set[str] = set()
    for path in sorted(business_dir.iterdir()):
        if path.suffix.casefold() not in {".xlsx", ".csv"}:
            continue
        try:
            for _, counter in _file_values(path):
                for raw, count in counter.items():
                    label = _label(raw)
                    key = (raw, label)
                    totals[key] += count
                    businesses[key].add(business_dir.name)
                    if label == "khong_ro":
                        unknown.add(raw)
        except Exception as error:
            print(f"[{business_dir.name}] lỗi {path.name}: {error}", file=sys.stderr)
    return unknown


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _render_report(
    split_dir: Path,
    totals: Counter[tuple[str, str]],
    businesses: dict[tuple[str, str], set[str]],
    unknown_by_business: dict[str, set[str]],
) -> str:
    lines = [
        f"# Chẩn đoán trạng thái — `{split_dir}`",
        "",
        "## 1. Bảng gộp toàn bộ",
        "",
        "| Giá trị gốc | Nhãn chuẩn | Tổng số dòng | Số business |",
        "|---|---|---:|---:|",
    ]
    for (raw, label), count in sorted(totals.items(), key=lambda item: (vn_norm(item[0][0]), item[0][1])):
        lines.append(f"| `{_markdown_cell(raw)}` | `{label}` | {count} | {len(businesses[(raw, label)])} |")

    lines.extend(["", "## 2. Danh sách nghi vấn", ""])
    suspicious = [
        (raw, label, count)
        for (raw, label), count in totals.items()
        if label == "khong_ro"
        or (label == "hoan_thanh" and any(part in vn_norm(raw) for part in SUSPICIOUS_PARTS))
    ]
    if suspicious:
        lines.extend(["| Giá trị gốc | Nhãn chuẩn | Tổng số dòng |", "|---|---|---:|"])
        for raw, label, count in sorted(suspicious, key=lambda item: (vn_norm(item[0]), item[1])):
            lines.append(f"| `{_markdown_cell(raw)}` | `{label}` | {count} |")
    else:
        lines.append("_Không có._")

    lines.extend(["", "## 3. Business có giá trị `khong_ro`", ""])
    if unknown_by_business:
        for business_id in sorted(unknown_by_business):
            values = ", ".join(f"`{_markdown_cell(value)}`" for value in sorted(unknown_by_business[business_id], key=vn_norm))
            lines.append(f"- `{business_id}`: {values}")
    else:
        lines.append("_Không có._")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split_dir", default="data/train")
    parser.add_argument("--out", default="outputs/diag_status_all.md")
    args = parser.parse_args()
    split_dir = Path(args.split_dir)
    if not split_dir.is_absolute():
        split_dir = ROOT / split_dir

    totals: Counter[tuple[str, str]] = Counter()
    businesses: dict[tuple[str, str], set[str]] = defaultdict(set)
    unknown_by_business: dict[str, set[str]] = {}
    business_dirs = _business_dirs(split_dir)
    for number, business_dir in enumerate(business_dirs, start=1):
        print(f"[{number}/{len(business_dirs)}] {business_dir.name}", flush=True)
        unknown = _scan_business(business_dir, totals, businesses)
        if unknown:
            unknown_by_business[business_dir.name] = unknown

    output = Path(args.out)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render_report(split_dir, totals, businesses, unknown_by_business), encoding="utf-8")
    print(f"Đã ghi {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
