"""Data-driven diagnostics for validating preprocessing and prompt construction.

The checks deliberately infer candidates from observed schemas and values.  They
do not rely on business-specific table, column, status, or question identifiers.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent_datamate.dataprocess.cleaner import clean_table
from ai_agent_datamate.dataprocess.loader import _read_source, load_business
from ai_agent_datamate.tool.prompts import build_prompt, _estimate_tokens


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")


def _numeric(frame: pd.DataFrame) -> list[str]:
    return [str(column) for column in frame.columns if pd.api.types.is_numeric_dtype(frame[column])]


def _date_columns(frame: pd.DataFrame) -> list[str]:
    return [
        str(column)
        for column in frame.columns
        if pd.api.types.is_datetime64_any_dtype(frame[column])
        or "date" in _norm(column)
        or "day" in _norm(column)
        or "time" in _norm(column)
    ]


def _likely_quantity(column: str) -> bool:
    text = _norm(column)
    return any(token in text for token in ("qty", "quantity", "count", "amount", "so_luong", "sl"))


def _likely_money(column: str) -> bool:
    text = _norm(column)
    return any(token in text for token in ("price", "cost", "amount", "total", "money", "revenue", "gia", "tien", "thanh"))


def _candidate_keys(frame: pd.DataFrame) -> list[str]:
    keys = []
    for column in frame.columns:
        series = frame[column].dropna()
        if len(series) and series.nunique() < len(series) and series.nunique() / len(series) <= 0.9:
            keys.append(str(column))
    return keys


def _status(result: bool | None) -> str:
    return "đúng" if result is True else "sai" if result is False else "không kết luận được"


def _load_raw_tables(business_dir: Path) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    for path in sorted(business_dir.iterdir()):
        if path.suffix.lower() not in {".csv", ".xlsx", ".xls", ".sqlite", ".db"}:
            continue
        for file_name, sheet, frame in _read_source(path):
            cleaned = clean_table(frame, file_name, sheet)
            if not cleaned.empty:
                suffix = f":{sheet}" if sheet else ""
                tables[f"{file_name}{suffix}"] = cleaned
    return tables


def _diagnose_tables(tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
    entity_pairs, repeated_amounts, conversion, arithmetic = [], [], [], []
    totals = []
    for table_name, frame in tables.items():
        for column in frame.columns:
            name = str(column)
            if name.casefold().endswith("_goc"):
                base = name[:-4]
                if base in frame.columns and pd.api.types.is_numeric_dtype(frame[base]) and frame[base].isna().mean() > 0.2:
                    entity_pairs.append({
                        "table": table_name, "numeric_column": base, "source_column": name,
                        "numeric_null_ratio": round(float(frame[base].isna().mean()), 4),
                        "source_non_null_ratio": round(float(frame[name].notna().mean()), 4),
                    })

        keys = _candidate_keys(frame)
        amounts = [column for column in _numeric(frame) if _likely_money(column)]
        for key in keys:
            for amount in amounts:
                grouped = frame.dropna(subset=[key, amount]).groupby(key, dropna=False)[amount]
                repeated = grouped.filter(lambda values: len(values) > 1)
                if len(repeated):
                    same_ratio = float(repeated.groupby(level=0).nunique().eq(1).mean())
                    if same_ratio > 0:
                        repeated_amounts.append({
                            "table": table_name, "order_key_candidate": key,
                            "amount_column": amount, "groups_with_repeats": int((grouped.size() > 1).sum()),
                            "same_value_ratio": round(same_ratio, 4),
                        })

        quantities = [column for column in _numeric(frame) if _likely_quantity(column)]
        for quantity in quantities:
            selected = frame[frame[quantity] >= 2]
            for column in _numeric(frame):
                if column == quantity or selected.empty:
                    continue
                distinct_by_quantity = selected.groupby(quantity)[column].nunique(dropna=True)
                if not distinct_by_quantity.empty:
                    conversion.append({
                        "table": table_name, "quantity_column": quantity, "candidate_column": column,
                        "rows_quantity_ge_2": int(len(selected)),
                        "distinct_values_by_quantity": {str(k): int(v) for k, v in distinct_by_quantity.items()},
                    })

        numeric = _numeric(frame)
        for unit_price in numeric:
            for quantity in quantities:
                for total in numeric:
                    if len({unit_price, quantity, total}) < 3:
                        continue
                    sample = frame[[unit_price, quantity, total]].dropna()
                    if sample.empty:
                        continue
                    expected = sample[unit_price] * sample[quantity]
                    relative_error = ((expected - sample[total]).abs() / expected.abs().clip(lower=1)).replace(
                        [float("inf")], pd.NA
                    ).dropna()
                    if len(relative_error):
                        arithmetic.append({
                            "table": table_name, "unit_price": unit_price,
                            "quantity": quantity, "total": total, "rows": int(len(relative_error)),
                            "within_1pct_ratio": round(float((relative_error <= 0.01).mean()), 4),
                            "max_relative_error": round(float(relative_error.max()), 6),
                        })

        marker_columns = [
            column for column in frame.columns
            if frame[column].astype(str).str.casefold().str.contains(r"total|sum|cong|tong", regex=True).any()
        ]
        if marker_columns:
            totals.append({"table": table_name, "marker_columns": marker_columns, "rows": int(len(frame))})

    return {
        "entity_pairs": entity_pairs,
        "repeated_amounts": repeated_amounts,
        "conversion_candidates": conversion,
        "arithmetic_candidates": arithmetic,
        "total_markers": totals,
    }


def _diagnose_prompt(split: Path, business_id: str, context: Any) -> dict[str, Any]:
    questions_path = split / "questions.jsonl"
    questions = [
        json.loads(line) for line in questions_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    selected = [item for item in questions if str(item.get("business_id")) == business_id][:5]
    rulebook = (split / "rulebook.md").read_text(encoding="utf-8") if (split / "rulebook.md").exists() else ""
    industry = re.search(r"(?ms)^## 6\. Theo ngành\b.*?(?=^##\s|\Z)", rulebook)
    industry_text = industry.group(0).strip() if industry else ""
    reports = []
    for item in selected:
        qctx, prompt = build_prompt(item["question"], item["as_of_date"], context, rulebook=rulebook)
        parts = qctx.prompt_part_tokens
        accounted = sum(parts.values())
        reports.append({
            "id": item.get("id"),
            "prompt_tokens_estimate": qctx.prompt_tokens_estimate,
            "prompt_part_tokens": parts,
            "unaccounted_tokens": qctx.prompt_tokens_estimate - accounted,
            "reduction_steps": qctx.prompt_reduction_steps,
            "industry_section_preserved": bool(industry_text) and industry_text in prompt,
            "readme_present_after_reduction": bool(context.readme_text) and context.readme_text[:100] in prompt,
            "prompt_sha256": __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest(),
        })
    return {"questions_checked": len(selected), "industry_section_found": bool(industry_text), "reports": reports}


def diagnose(split_dir: Path, business_id: str) -> dict[str, Any]:
    business_dir = split_dir / "data" / business_id
    if not business_dir.is_dir():
        return {"business_id": business_id, "status": "không có dữ liệu", "path": str(business_dir)}
    raw = _load_raw_tables(business_dir)
    context = load_business(business_dir, business_id)
    table_report = _diagnose_tables(raw)
    return {
        "business_id": business_id,
        "status": "đã kiểm tra",
        "files": [path.name for path in sorted(business_dir.iterdir()) if path.is_file()],
        "raw_tables": {name: {"rows": len(frame), "columns": list(frame.columns)} for name, frame in raw.items()},
        "cleaned_tables": {
            name: {"rows": profile.row_count, "columns": list(profile.columns)}
            for name, profile in context.tables.items()
        },
        "hypotheses": table_report,
        "prompt": _diagnose_prompt(split_dir, business_id, context),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose data assumptions without business-specific rules.")
    parser.add_argument("--split_dir", default="data/train")
    parser.add_argument("--business_id", action="append", required=True)
    args = parser.parse_args()
    split = Path(args.split_dir)
    reports = [diagnose(split, business_id) for business_id in args.business_id]
    print(json.dumps({"split_dir": str(split), "reports": reports}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
