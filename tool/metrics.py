"""Grounded SQL builders for common order metrics."""

from __future__ import annotations

import re
from typing import Any


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONEY_HINTS = ("thanh_tien", "doanh_thu", "revenue", "amount")
_INVOICE_MONEY_HINTS = ("sau_giam_gia", "tong_don", "tong_hoa_don", "phai_tra")
_ORDER_HINTS = ("ma_don", "so_don", "don_hang", "order", "invoice", "hoa_don")
_STATUS_HINTS = ("status", "trang_thai", "tinh_trang", "state")
_CODE_VALUE = re.compile(r"^[A-Za-z]+\d+$")


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _field(profile: Any, name: str, default: Any = None) -> Any:
    if isinstance(profile, dict):
        return profile.get(name, default)
    return getattr(profile, name, default)


def _columns(profile: Any) -> dict[str, Any]:
    columns = _field(profile, "columns", {})
    return columns if isinstance(columns, dict) else {}


def _tables(conn: Any) -> list[str]:
    return [
        str(row[0]) for row in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type IN ('BASE TABLE', 'VIEW')"
        ).fetchall()
    ]


def _period(period: Any) -> tuple[str, str]:
    if isinstance(period, dict):
        start, end = period.get("start"), period.get("end")
    else:
        start, end = period
    if not isinstance(start, str) or not isinstance(end, str) or not _DATE.fullmatch(start) or not _DATE.fullmatch(end):
        raise ValueError("period must contain YYYY-MM-DD start and end")
    if start > end:
        raise ValueError("period start must not be after end")
    return start, end


def _choose(candidates: list[str], hints: tuple[str, ...]) -> str | None:
    ranked = [(sum(hint in name.casefold() for hint in hints), name) for name in candidates]
    ranked = [item for item in ranked if item[0]]
    return max(ranked, default=(0, None))[1]


def _money_columns(names: list[str], numeric: set[str]) -> tuple[list[str], list[str]]:
    line = [
        column for column in numeric
        if "thanh_tien" in column.casefold()
        or any(hint in column.casefold() for hint in ("line_amount", "line_total"))
    ]
    invoice = [column for column in numeric if any(
        hint in column.casefold() for hint in _INVOICE_MONEY_HINTS
    )]
    return line, invoice


def _formula_revenue(conn: Any, table: str, names: list[str], numeric: set[str]) -> tuple[str | None, float]:
    quantity = _choose([column for column in numeric if any(
        hint in column.casefold() for hint in ("so_luong", "quantity", "qty")
    )], ("so_luong", "quantity", "qty"))
    price = _choose([column for column in numeric if any(
        hint in column.casefold() for hint in ("don_gia", "unit_price", "gia_ban")
    )], ("don_gia", "unit_price", "gia_ban"))
    discount = _choose([column for column in numeric if any(
        hint in column.casefold() for hint in ("chiet_khau", "giam_gia", "discount")
    )], ("chiet_khau", "giam_gia", "discount"))
    if not quantity or not price:
        return None, 0.0
    amount_candidates = [
        column for column in numeric
        if column not in {quantity, price, discount}
        and column not in _money_columns(names, numeric)[1]
    ]
    candidate_column = _choose(amount_candidates, _MONEY_HINTS)
    if not candidate_column:
        return None, 0.0
    expression = f"{_q(quantity)} * {_q(price)}"
    if discount:
        expression += f" - COALESCE({_q(discount)}, 0)"
    try:
        matched, total = conn.execute(
            f"SELECT COUNT(*) FILTER (WHERE ABS(({expression}) - candidate) <= "
            f"GREATEST(1, ABS(candidate) * 0.01)), COUNT(*) FROM "
            f"(SELECT {expression} AS expected, {_q(candidate_column)} AS candidate FROM {_q(table)} "
            f"WHERE {_q(quantity)} IS NOT NULL AND {_q(price)} IS NOT NULL) values"
        ).fetchone()
    except Exception:
        return None, 0.0
    ratio = float(matched / total) if total else 0.0
    return candidate_column, ratio


def _dedupe_source(cfg: dict[str, Any], table: str) -> str:
    if cfg.get("dedupe"):
        return f"(SELECT DISTINCT * FROM {table})"
    return table


def _find_status_column(conn: Any, table: str, names: list[str]) -> str | None:
    for column in [*([ "trang_thai_chuan"] if "trang_thai_chuan" in names else []),
                   *[name for name in names if name != "trang_thai_chuan"]]:
        values = [
            str(row[0]).strip() for row in conn.execute(
                f"SELECT DISTINCT {_q(column)} FROM {_q(table)} "
                f"WHERE {_q(column)} IS NOT NULL LIMIT 31"
            ).fetchall()
        ]
        if len(values) > 30 or not values:
            continue
        normalized = [value.casefold().replace("_", " ") for value in values]
        if any(_CODE_VALUE.fullmatch(value.replace(" ", "")) for value in normalized):
            continue
        if any("dang hoat dong" in value or value == "ma don hang" for value in normalized):
            continue
        if column == "trang_thai_chuan" or any(
            any(token in value for token in ("huy", "tra", "hoan thanh", "dang giao", "cho giao"))
            for value in normalized
        ) or any(hint in column.casefold() for hint in _STATUS_HINTS):
            return column
    return None


def infer_metric_config(conn: Any, profile: Any = None) -> dict[str, Any]:
    """Infer metric sources from the loaded schema; refuse weak evidence."""
    try:
        tables = _tables(conn)
        name = str(_field(profile, "name", "") or "")
        candidates = [name] if name in tables else tables
        public_candidates = [
            table for table in candidates
            if not table.endswith("_raw") and f"{table}_raw" in tables
        ]
        candidates = public_candidates or candidates
        merged = [table for table in candidates if not re.search(r"_t?\d{1,2}_\d{4}$|_\d{4}_\d{1,2}$", table)]
        candidates = merged or candidates
        selected = next(
            (table for table in candidates if "ngay" in {
                str(row[0]) for row in conn.execute(f"DESCRIBE {_q(table)}").fetchall()
            }),
            None,
        )
        if selected is None:
            return {"ok": False, "reason": "no table/view with normalized ngay column"}
        description = conn.execute(f"DESCRIBE {_q(selected)}").fetchall()
        names = [str(row[0]) for row in description]
        profile_columns = _columns(profile)
        roles = _field(profile, "roles", {}) or {}
        measures = _field(profile, "measures", {}) or {}
        numeric = {
            column for column, dtype, *_ in description
            if any(token in str(dtype).upper() for token in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC"))
        }
        line_money, invoice_money = _money_columns(names, numeric)
        revenue = next((column for column in line_money if roles.get(column, {}).get("role") == "line_amount"), None)
        revenue = revenue or (line_money[0] if line_money else None)
        revenue_reason = "line amount column name"
        if revenue is None:
            revenue = next((column for column in numeric if roles.get(column, {}).get("role") == "line_amount"
                            and not any(hint in column.casefold() for hint in _INVOICE_MONEY_HINTS)), None)
            revenue_reason = "profiled line amount"
        order_key = measures.get("order_count", "") if isinstance(measures, dict) else ""
        match = re.search(r"\(([^)]+)\)", str(order_key))
        order_key = match.group(1).strip() if match else None
        if order_key not in names:
            order_key = next((column for column, info in roles.items() if info.get("role") == "order_key" and column in names), None)
        order_key = order_key or _choose(names, _ORDER_HINTS)
        if not order_key or "ngay" not in names:
            return {"ok": False, "reason": "missing normalized date, revenue, or order key"}
        grain = _field(profile, "grain")
        if grain not in {"line", "order"}:
            distinct, total = conn.execute(
                f"SELECT COUNT(DISTINCT {_q(order_key)}), COUNT(*) FROM {_q(selected)} "
                f"WHERE {_q(order_key)} IS NOT NULL"
            ).fetchone()
            grain = "line" if total and distinct < total else "order"
        if revenue is None:
            formula_revenue, formula_ratio = _formula_revenue(conn, selected, names, numeric)
            if formula_ratio >= 0.8:
                revenue = formula_revenue
                revenue_reason = "quantity * unit price - line discount validated on %.1f%% of rows" % (formula_ratio * 100)
        if revenue is None:
            generic_money = [
                column for column in numeric
                if column not in invoice_money and any(
                    hint in column.casefold() for hint in _MONEY_HINTS
                )
            ]
            if generic_money:
                revenue = _choose(generic_money, _MONEY_HINTS)
                revenue_reason = "generic amount column without invoice-total hint"
        if revenue is None and grain == "order":
            revenue = invoice_money[0] if invoice_money else _choose(list(numeric), _MONEY_HINTS)
            if revenue:
                revenue_reason = "order-grain total"
        if revenue is None and grain == "line" and invoice_money:
            repeated_invoice = conn.execute(
                f"SELECT COUNT(*) AS rows_per_order, COUNT(DISTINCT {_q(invoice_money[0])}) "
                f"FROM {_q(selected)} WHERE {_q(order_key)} IS NOT NULL "
                f"GROUP BY {_q(order_key)} HAVING COUNT(*) > 1"
            ).fetchall()
            same_ratio = (
                sum(row[1] == 1 for row in repeated_invoice) / len(repeated_invoice)
                if repeated_invoice else 0.0
            )
            if same_ratio >= 0.95:
                revenue = invoice_money[0]
                revenue_reason = "invoice total, MAX per order after repeated-value validation"
        if not revenue:
            return {"ok": False, "reason": "no reliable line-level revenue column"}
        status_column = _find_status_column(conn, selected, names)
        if status_column is None:
            return {"ok": False, "reason": "no status column"}
        aggregation = "SUM"
        if grain == "line":
            repeated = conn.execute(
                f"SELECT COUNT(*) AS rows_per_order, COUNT(DISTINCT {_q(revenue)}) AS values_per_order "
                f"FROM {_q(selected)} WHERE {_q(order_key)} IS NOT NULL "
                f"GROUP BY {_q(order_key)} HAVING COUNT(*) > 1"
            ).fetchall()
            if repeated:
                same_ratio = sum(row[1] == 1 for row in repeated) / len(repeated)
                if same_ratio >= 0.95:
                    aggregation = "MAX_PER_ORDER"
        min_date, max_date, active_months = None, None, set()
        try:
            min_date, max_date = conn.execute(
                f"SELECT MIN(TRY_CAST({_q('ngay')} AS DATE)), MAX(TRY_CAST({_q('ngay')} AS DATE)) "
                f"FROM {_q(selected)} WHERE {_q('ngay')} IS NOT NULL"
            ).fetchone()
            month_rows = conn.execute(
                f"SELECT DISTINCT strftime(TRY_CAST({_q('ngay')} AS DATE), '%Y-%m') "
                f"FROM {_q(selected)} WHERE {_q('ngay')} IS NOT NULL"
            ).fetchall()
            active_months = {str(row[0]) for row in month_rows if row[0] is not None}
            min_date = str(min_date)[:10] if min_date is not None else None
            max_date = str(max_date)[:10] if max_date is not None else None
        except Exception:
            pass
        return {
            "ok": True,
            "table": selected,
            "date_column": "ngay",
            "revenue_column": revenue,
            "order_column": order_key,
            "grain": grain,
            "status_column": status_column,
            "status_condition": (
                f"({_q(status_column)} IS NULL OR {_q(status_column)} NOT IN ('huy', 'tra_hang'))"
            ),
            "revenue_aggregation": aggregation,
            "revenue_column_reason": revenue_reason,
            "dedupe": False,
            "min_date": min_date,
            "max_date": max_date,
            "active_months": sorted(active_months),
        }
    except Exception as error:
        return {"ok": False, "reason": str(error)}


def infer_metric_config_for_context(context: Any) -> dict[str, Any]:
    """Infer metrics by scanning every table/view in a loaded business."""
    return infer_metric_config(context.con, None)


def _metric_expression(cfg: dict[str, Any], metric: str) -> str:
    if metric in {"orders", "cancelled_orders"}:
        if metric == "cancelled_orders":
            return f"COUNT(DISTINCT {_q(cfg['order_column'])})"
        return f"COUNT(DISTINCT {_q(cfg['order_column'])})"
    if metric not in {"revenue", "cancel_rate"}:
        raise ValueError("unsupported metric")
    if cfg.get("grain") == "line" and cfg.get("revenue_aggregation") == "MAX_PER_ORDER":
        return f"SUM(order_totals.metric_value)"
    return f"SUM({_q(cfg['revenue_column'])})"


def build_metric_sql(cfg: dict[str, Any], metric: str, start: str, end: str) -> str:
    start, end = _period((start, end))
    if not cfg.get("ok"):
        raise ValueError(cfg.get("reason", "invalid metric config"))
    table, date_column = _q(cfg["table"]), _q(cfg["date_column"])
    source = _dedupe_source(cfg, table)
    where = f"{date_column} BETWEEN DATE '{start}' AND DATE '{end}'"
    status = _q(cfg["status_column"])
    order = _q(cfg["order_column"])
    if metric == "cancel_rate":
        numerator = build_metric_sql(cfg, "cancelled_orders", start, end).rstrip(";")
        denominator = (
            f"SELECT COUNT(DISTINCT {order}) AS value FROM {source} "
            f"WHERE {where}"
        )
        return (
            f"WITH cancelled AS ({numerator}), total AS ({denominator}) "
            "SELECT ROUND(cancelled.value * 100.0 / NULLIF(total.value, 0), 2) AS value "
            "FROM cancelled CROSS JOIN total"
        )
    if metric == "cancelled_orders":
        where += f" AND {status} = 'huy'"
        if cfg.get("grain") == "line":
            where += (
                f" AND {order} NOT IN (SELECT {order} FROM {source} "
                f"WHERE {status} = 'tra_hang')"
            )
    elif cfg.get("grain") == "line":
        where = (
            f"{where} AND {order} NOT IN (SELECT {order} FROM {source} "
            f"WHERE {status} IN ('huy', 'tra_hang'))"
        )
    elif cfg.get("status_condition"):
        where += f" AND {cfg['status_condition']}"
    if metric == "revenue" and cfg.get("grain") == "line" and cfg.get("revenue_aggregation") == "MAX_PER_ORDER":
        return (
            f"WITH order_totals AS (SELECT {_q(cfg['order_column'])}, "
            f"MAX({_q(cfg['revenue_column'])}) AS metric_value FROM {source} "
            f"WHERE {where} GROUP BY {_q(cfg['order_column'])}) "
            "SELECT SUM(order_totals.metric_value) AS value FROM order_totals"
        )
    return f"SELECT {_metric_expression(cfg, metric)} AS value FROM {source} WHERE {where}"


def build_compare_sql(cfg: dict[str, Any], metric: str, p1: Any, p2: Any) -> str:
    start1, end1 = _period(p1)
    start2, end2 = _period(p2)
    first = build_metric_sql(cfg, metric, start1, end1).rstrip(";")
    second = build_metric_sql(cfg, metric, start2, end2).rstrip(";")
    return (
        f"WITH first_period AS ({first}), second_period AS ({second}) "
        "SELECT CASE WHEN second_period.value = 0 THEN NULL "
        "ELSE ROUND((first_period.value - second_period.value) * 100.0 "
        "/ ABS(second_period.value), 2) END AS percent_change "
        "FROM first_period CROSS JOIN second_period"
    )


__all__ = [
    "infer_metric_config",
    "infer_metric_config_for_context",
    "build_metric_sql",
    "build_compare_sql",
]
