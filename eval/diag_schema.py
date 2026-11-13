"""Compact schema diagnostics for the metric-evaluation businesses."""

from __future__ import annotations

import gc
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, "") or str(Path.cwd()) == str(Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent_datamate.dataprocess import load_business
from ai_agent_datamate.tool.metrics import infer_metric_config


ROOT = Path(__file__).resolve().parents[1]
BUSINESSES = ("biz_109", "biz_110", "biz_113", "biz_118", "biz_123")
NUMERIC_TYPES = ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC", "HUGEINT")
MONEY_HINTS = ("thanh_tien", "tong_tien", "doanh_thu", "revenue", "amount", "total", "tien")
ORDER_HINTS = ("ma_don", "so_don", "don_hang", "order", "invoice", "hoa_don")
STATUS_HINTS = ("trang_thai", "status", "state", "tinh_trang", "ket_qua")


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _short(value: Any, limit: int = 34) -> str:
    if value is None:
        return "NULL"
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _table_rows(conn: Any, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0])


def _describe(conn: Any, table: str) -> list[tuple[Any, ...]]:
    return conn.execute(f"DESCRIBE {_q(table)}").fetchall()


def _column_stats(conn: Any, table: str, column: str) -> tuple[int, list[Any], float]:
    sql = (
        f"SELECT COUNT(DISTINCT {_q(column)}), "
        f"LIST({_q(column)}) FILTER (WHERE {_q(column)} IS NOT NULL)[:3], "
        f"COALESCE(AVG(CASE WHEN {_q(column)} IS NULL THEN 1.0 ELSE 0.0 END), 0.0) "
        f"FROM {_q(table)}"
    )
    distinct, samples, null_ratio = conn.execute(sql).fetchone()
    return int(distinct), list(samples or []), float(null_ratio)


def _date_parse_ratio(conn: Any, table: str, column: str, dtype: str) -> float | None:
    if "DATE" in dtype.upper() or "TIMESTAMP" in dtype.upper():
        return 1.0
    if "VARCHAR" not in dtype.upper() and "TEXT" not in dtype.upper():
        return None
    value = conn.execute(
        f"SELECT COALESCE(AVG(CASE WHEN {_q(column)} IS NULL THEN NULL "
        f"WHEN TRY_CAST({_q(column)} AS TIMESTAMP) IS NOT NULL THEN 1.0 ELSE 0.0 END), 0.0) "
        f"FROM {_q(table)}"
    ).fetchone()[0]
    return float(value)


def _max_abs(conn: Any, table: str, column: str) -> float | None:
    value = conn.execute(
        f"SELECT MAX(ABS(TRY_CAST({_q(column)} AS DOUBLE))) FROM {_q(table)}"
    ).fetchone()[0]
    return float(value) if value is not None else None


def _repetition(conn: Any, table: str, column: str) -> tuple[int, int]:
    repeated, max_count = conn.execute(
        f"SELECT COUNT(*) FILTER (WHERE n > 1), COALESCE(MAX(n), 0) "
        f"FROM (SELECT {_q(column)}, COUNT(*) AS n FROM {_q(table)} "
        f"WHERE {_q(column)} IS NOT NULL GROUP BY {_q(column)})"
    ).fetchone()
    return int(repeated), int(max_count)


def _join(items: list[str], width: int = 115) -> list[str]:
    lines: list[str] = []
    current = ""
    for item in items:
        if current and len(current) + len(item) + 2 > width:
            lines.append(current)
            current = item
        else:
            current = item if not current else f"{current}; {item}"
    if current:
        lines.append(current)
    return lines


def _profile_for(context: Any) -> Any:
    profiles = getattr(context, "tables", {})
    preferred = [
        name for name in profiles
        if name == "donhang_chitiet"
        or ("ngay" in profiles[name].columns and not re.search(r"_t?\d{1,2}_\d{4}$|_\d{4}_\d{1,2}$", name))
    ]
    return profiles[preferred[0]] if preferred else next(iter(profiles.values()), None)


def _diagnose_business(business_id: str) -> list[str]:
    context = None
    lines = [f"## {business_id}"]
    try:
        context = load_business(ROOT / "data" / "train" / "data" / business_id, business_id)
        conn = context.con
        tables = conn.execute(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type IN ('BASE TABLE', 'VIEW') "
            "ORDER BY table_name"
        ).fetchall()
        table_rows = {str(name): _table_rows(conn, str(name)) for name, _ in tables}
        lines.append("- Bảng/view: " + ", ".join(f"`{name}`({table_rows[name]})" for name, _ in tables))

        candidates: dict[str, list[str]] = {"date": [], "money": [], "order": [], "status": []}
        table_candidates: list[str] = []
        schema_items: list[str] = []
        for table, _ in tables:
            table = str(table)
            descriptions = _describe(conn, table)
            column_items: list[str] = []
            numeric_values: list[tuple[str, float]] = []
            local_candidates: dict[str, list[str]] = {
                "date": [], "money": [], "order": [], "status": []
            }
            for name, dtype, *_ in descriptions:
                name, dtype = str(name), str(dtype)
                distinct, samples, null_ratio = _column_stats(conn, table, name)
                item = f"`{name}`:{dtype},d={distinct},s={_short(samples)},n={null_ratio:.0%}"
                column_items.append(item)
                date_ratio = _date_parse_ratio(conn, table, name, dtype)
                if date_ratio is not None and date_ratio >= 0.8:
                    candidate = f"{name}({date_ratio:.0%})"
                    candidates["date"].append(f"{table}.{candidate}")
                    local_candidates["date"].append(candidate)
                if any(token in dtype.upper() for token in NUMERIC_TYPES):
                    maximum = _max_abs(conn, table, name)
                    if maximum is not None:
                        numeric_values.append((name, maximum))
                if "VARCHAR" in dtype.upper() or "TEXT" in dtype.upper():
                    repeated, max_count = _repetition(conn, table, name)
                    if distinct < table_rows[table] and repeated and max_count > 1:
                        candidate = f"{name}(d={distinct},rep={repeated})"
                        candidates["order"].append(f"{table}.{candidate}")
                        local_candidates["order"].append(candidate)
                if distinct <= 20 and (
                    distinct <= max(table_rows[table] * 0.2, 1)
                    or any(token in name.casefold() for token in STATUS_HINTS)
                ):
                    candidate = f"{name}(d={distinct})"
                    candidates["status"].append(f"{table}.{candidate}")
                    local_candidates["status"].append(candidate)
            for name, maximum in sorted(numeric_values, key=lambda item: item[1], reverse=True):
                if any(token in name.casefold() for token in MONEY_HINTS) or maximum >= 1000:
                    candidate = f"{name}(max={maximum:g})"
                    candidates["money"].append(f"{table}.{candidate}")
                    local_candidates["money"].append(candidate)
            schema_items.append(f"`{table}`({table_rows[table]}): " + "; ".join(column_items))
            table_candidates.append(
                f"`{table}`: " + " ".join(
                    f"{role}=[{','.join(local_candidates[role]) or '-'}]"
                    for role in ("date", "money", "order", "status")
                )
            )
        lines.append("- Cột: " + " | ".join(schema_items))
        lines.append("- Ứng viên/bảng: " + " | ".join(table_candidates))
        profile = _profile_for(context)
        config = infer_metric_config(conn, profile) if profile is not None else {
            "ok": False, "reason": "no profile"
        }
        lines.append(f"- infer_metric_config: `{json.dumps(config, ensure_ascii=False, default=str)}`")
        if not config.get("ok") or business_id == "biz_113":
            profile_roles = getattr(profile, "roles", {}) if profile is not None else {}
            profile_measures = getattr(profile, "measures", {}) if profile is not None else {}
            lines.append(
                "- Lý do/chọn: "
                f"reason={config.get('reason', 'ok')}; "
                f"roles={json.dumps(profile_roles, ensure_ascii=False)}; "
                f"measures={json.dumps(profile_measures, ensure_ascii=False)}"
            )
    except Exception as error:
        lines.append(f"- LỖI: {type(error).__name__}: {error}")
    finally:
        if context is not None:
            del context
        gc.collect()
    return lines


def main() -> int:
    report = ["# Schema diagnostics", ""]
    for business_id in BUSINESSES:
        report.extend(_diagnose_business(business_id))
        report.append("")
    (ROOT / "outputs" / "diag_schema.md").write_text("\n".join(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
