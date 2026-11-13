"""Diagnose date-related NULL/0 results in generated SQL."""

from __future__ import annotations

import json
import re
import sys
import argparse
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

try:
    from dataprocess import load_business
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from ai_agent_datamate.dataprocess import load_business


ROOT = Path(__file__).resolve().parents[1]
DEBUG_PATH = ROOT / "outputs" / "debug" / "debug.jsonl"
GOLD_PATH = ROOT / "data" / "train" / "answers.jsonl"
DATA_PATH = ROOT / "data" / "train" / "data"
OUT_PATH = ROOT / "outputs" / "diag_dates.md"
DATE_WORDS = ("ngay", "date", "thoi_gian")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _dates(text: str) -> list[date]:
    result = []
    for raw in re.findall(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b", text):
        parts = re.split(r"[-/]", raw)
        try:
            y, m, d = (map(int, parts) if len(parts[0]) == 4 else (int(parts[2]), int(parts[1]), int(parts[0])))
            result.append(date(y, m, d))
        except ValueError:
            pass
    for month, year in re.findall(r"tháng\s*(\d{1,2})\s*(?:năm\s*)?(\d{4})", text.casefold()):
        try:
            result.append(date(int(year), int(month), 1))
            result.append(date(int(year), int(month), 28))
        except ValueError:
            pass
    return result


def _table_names(sql: str, available: set[str]) -> list[str]:
    found = []
    for match in re.finditer(r"\b(?:from|join)\s+([`\"A-Za-z_][\w$.-]*)", sql, re.I):
        name = match.group(1).strip("`\"")
        key = next((item for item in available if item.casefold() == name.casefold()), None)
        if key and key not in found:
            found.append(key)
    return found


def _column_names(con: Any, table: str) -> list[tuple[str, str]]:
    rows = con.execute(f"DESCRIBE {_quote(table)}").fetchall()
    return [(str(row[0]), str(row[1])) for row in rows
            if any(word in str(row[0]).casefold() for word in DATE_WORDS)]


def _column_stats(con: Any, table: str, column: str) -> dict[str, Any]:
    qtable, qcolumn = _quote(table), _quote(column)
    typeof = con.execute(f"SELECT typeof({qcolumn}) FROM {qtable} WHERE {qcolumn} IS NOT NULL LIMIT 1").fetchone()
    samples = con.execute(
        f"SELECT {qcolumn} FROM {qtable} WHERE {qcolumn} IS NOT NULL LIMIT 3"
    ).fetchall()
    minimum, maximum = con.execute(f"SELECT MIN({qcolumn}), MAX({qcolumn}) FROM {qtable}").fetchone()
    return {
        "type": typeof[0] if typeof else "UNKNOWN",
        "samples": [row[0] for row in samples],
        "min": minimum,
        "max": maximum,
    }


def _date_range(stats: dict[str, Any]) -> tuple[date, date] | None:
    values = [stats.get("min"), stats.get("max")]
    parsed = []
    for value in values:
        match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", str(value))
        if not match:
            match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(value))
            parts = (match.group(3), match.group(2), match.group(1)) if match else ()
        else:
            parts = match.groups()
        try:
            parsed.append(date(*(int(part) for part in parts)))
        except (TypeError, ValueError):
            return None
    return (parsed[0], parsed[1]) if len(parsed) == 2 else None


def _shard_prefix(name: str) -> str:
    return re.sub(r"(?:_t?\d{1,2}_\d{4}|_\d{4}_\d{1,2}|_\d{4})$", "", name.casefold())


def _format(value: Any) -> str:
    return str(value).replace("\n", " ") if value is not None else "NULL"


def _diagnose(record: dict[str, Any], gold: dict[str, Any], context: Any, available: set[str]) -> tuple[str, Counter]:
    counts: Counter = Counter()
    sql = str((record.get("candidates") or [{}])[0].get("sql") or "")
    tables = _table_names(sql, available)
    question_dates = _dates(f"{record.get('as_of_date', '')} {record.get('question', '')}")
    parts = [
        f"## {record.get('id')}",
        f"- **Câu hỏi:** {record.get('question', '')}",
        f"- **Gold:** `{json.dumps(gold, ensure_ascii=False)}`",
        f"- **SQL:** `{sql.replace(chr(10), ' ')}`",
    ]
    if not tables:
        return "\n".join(parts + ["- **Bảng đang dùng:** không trích xuất được"]), Counter({"không xác định bảng": 1})
    for table in tables:
        parts.append(f"- **Bảng đang dùng:** `{table}`")
        sibling_names = [name for name in available if _shard_prefix(name) == _shard_prefix(table)]
        sibling_ranges = {}
        for sibling in sorted(sibling_names):
            try:
                date_columns = _column_names(context.con, sibling)
                ranges = [_date_range(_column_stats(context.con, sibling, column)) for column, _ in date_columns]
                ranges = [item for item in ranges if item]
                if ranges:
                    sibling_ranges[sibling] = (min(item[0] for item in ranges), max(item[1] for item in ranges))
            except Exception:
                continue
        if len(sibling_ranges) > 1:
            parts.append("- **Bảng shard:** " + ", ".join(
                f"`{name}` [{bounds[0]}, {bounds[1]}]" for name, bounds in sorted(sibling_ranges.items())
            ))
        for column, _ in _column_names(context.con, table):
            try:
                stats = _column_stats(context.con, table, column)
                parts.append(
                    f"  - `{column}` ({stats['type']}): mẫu "
                    f"{', '.join(_format(value) for value in stats['samples'])}; "
                    f"MIN={_format(stats['min'])}, MAX={_format(stats['max'])}"
                )
                text_samples = " ".join(map(str, stats["samples"]))
                if "VARCHAR" in stats["type"].upper():
                    counts["cột ngày VARCHAR"] += 1
                if re.search(r"\d{2}:\d{2}(?::\d{2})?", text_samples):
                    counts["giá trị có giờ"] += 1
                if re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text_samples):
                    counts["định dạng dd/mm/yyyy"] += 1
                bounds = _date_range(stats)
                if bounds and any(day < bounds[0] or day > bounds[1] for day in question_dates):
                    counts["kỳ/as_of_date ngoài khoảng bảng"] += 1
            except Exception as error:
                parts.append(f"  - `{column}`: lỗi `{error}`")
        if sibling_ranges and table in sibling_ranges and question_dates:
            matching = [name for name, bounds in sibling_ranges.items()
                        if any(bounds[0] <= day <= bounds[1] for day in question_dates)]
            if matching and table not in matching:
                parts.append(f"- **Shard đúng nghi vấn:** `{', '.join(sorted(matching))}`")
                counts["query nhầm shard"] += 1
    flags = [name for name in counts if counts[name]]
    parts.append("- **Dấu hiệu nghi vấn:** " + (", ".join(flags) if flags else "không thấy"))
    return "\n".join(parts), counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5, help="Số câu tối đa cần chẩn đoán (mặc định: 5)")
    args = parser.parse_args()
    summary: Counter = Counter()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    sections = []
    if not DEBUG_PATH.exists():
        OUT_PATH.write_text(f"# Date diagnostics\n\nKhông tìm thấy `{DEBUG_PATH}`.\n", encoding="utf-8")
        return 0
    gold = {str(item["id"]): item for line in GOLD_PATH.read_text(encoding="utf-8").splitlines() if line.strip()
            for item in [json.loads(line)]}
    contexts: dict[str, Any] = {}
    processed = 0
    for line in DEBUG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if args.limit >= 0 and processed >= args.limit:
            break
        record: dict[str, Any] = {}
        try:
            record = json.loads(line)
            if str(record.get("id")) not in gold:
                continue
            business_id = str(record["business_id"])
            if business_id not in contexts:
                contexts[business_id] = load_business(DATA_PATH / business_id, business_id)
            available = {str(row[0]) for row in contexts[business_id].con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()}
            section, counts = _diagnose(record, gold[str(record["id"])], contexts[business_id], available)
            sections.append(section)
            summary.update(counts)
            processed += 1
        except Exception as error:
            sections.append(f"## {record.get('id', 'unknown')}\n- **Lỗi:** `{error}`")
    lines = ["# Date diagnostics", "", *sum(([section, ""] for section in sections), [])]
    lines += ["## Tổng hợp", "", "| Dấu hiệu | Số lần |", "|---|---:|"]
    lines += [f"| {name} | {summary[name]} |" for name in sorted(summary)]
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
