"""Static validation and safe execution for generated DuckDB SQL."""

from __future__ import annotations

import difflib
import re
import threading
from typing import Any, Iterable

try:
    import sqlglot
    from sqlglot import exp
except ImportError:  # Keep imports usable before optional dependencies are installed.
    sqlglot = exp = None

from ..llm.generator import Candidate

FORBIDDEN_WORDS = re.compile(
    r"\b(?:attach|copy|create|delete|drop|export|install|insert|load|pragma|"
    r"read_csv|read_json|read_parquet|reset|set|update|use)\b",
    re.IGNORECASE,
)
HARD_FLAGS = frozenset({"UNSAFE_SQL", "MULTI_STATEMENT", "CARTESIAN", "RAW_TABLE"})


def _table_names(tree: Any) -> tuple[set[str], set[str]]:
    tables: set[str] = set()
    ctes: set[str] = set()
    if exp is None:
        return tables, ctes
    for cte in tree.find_all(exp.CTE):
        if cte.alias:
            ctes.add(cte.alias.lower())
    for table in tree.find_all(exp.Table):
        tables.add(table.name.lower())
    return tables, ctes


def _closest(name: str, names: Iterable[str]) -> str:
    matches = difflib.get_close_matches(name, list(names), n=3, cutoff=0.35)
    return ", ".join(matches) if matches else "(không có)"


def static_check(sql: str, ctx: Any) -> tuple[list[str], list[str]]:
    """Return hard errors and semantic flags without executing *sql*."""
    errors: list[str] = []
    flags: list[str] = []
    if not isinstance(sql, str) or not sql.strip():
        return ["SQL rỗng"], flags
    if FORBIDDEN_WORDS.search(sql):
        errors.append("Chỉ cho phép SELECT; SQL chứa lệnh hoặc hàm bị cấm")
    if ";" in sql.rstrip().rstrip(";"):
        flags.append("MULTI_STATEMENT")
        errors.append("Chỉ được một câu lệnh SELECT")

    table_profiles = getattr(ctx, "tables", {}) or {}
    allowed = {str(name).lower() for name in table_profiles}
    if sqlglot is None:
        if not re.match(r"^\s*(?:select|with)\b", sql, re.I):
            errors.append("Chỉ cho phép SELECT hoặc WITH")
        for name in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w]*)", sql, re.I):
            if name.lower() not in allowed:
                errors.append(f"Catalog Error: bảng '{name}' không tồn tại. Gần nhất: {_closest(name.lower(), allowed)}")
        if re.search(r"\bcross\s+join\b|\bjoin\s+[^\s,]+\s*(?:where|$)", sql, re.I):
            flags.append("CARTESIAN")
            errors.append("JOIN phải có điều kiện")
        return errors, flags

    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as error:
        return [f"Parser Error: {error}"], flags
    if len(statements) != 1:
        errors.append("Chỉ được một câu lệnh SELECT")
        return errors, flags
    tree = statements[0]
    forbidden_types = tuple(
        item for item in (exp.Create, exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Command, exp.Copy)
        if item is not None
    )
    if not isinstance(tree, (exp.Select, exp.Union, exp.With)) or (forbidden_types and tree.find(*forbidden_types)):
        errors.append("Chỉ cho phép SELECT")
    names, ctes = _table_names(tree)
    for name in names - ctes:
        if name not in allowed:
            errors.append(f"Catalog Error: bảng '{name}' không tồn tại. Gần nhất: {_closest(name, allowed)}")
        if name.endswith("_raw"):
            flags.append("RAW_TABLE")
            errors.append("Không được truy cập bảng raw")
    joins = list(tree.find_all(exp.Join))
    if any(join.args.get("on") is None for join in joins) or re.search(r"\bcross\s+join\b", sql, re.IGNORECASE):
        flags.append("CARTESIAN")
        errors.append("JOIN phải có điều kiện")
    return errors, flags


def semantic_lint(sql: str, ctx: Any, question: str = "") -> list[str]:
    """Return non-fatal semantic warnings that can lower voting confidence."""
    flags: list[str] = []
    normalized_question = question.lower()
    profiles = getattr(ctx, "tables", {}) or {}
    if re.search(r"\b(ngày|ngay|tuần|tuan|tháng|thang|quý|quy|năm|nam|hôm qua|hom qua)\b", normalized_question):
        if not re.search(r"\b(ngay|date|thoi_gian|timestamp)\b", sql, re.IGNORECASE):
            flags.append("NO_DATE_FILTER")
    if re.search(r"\b(doanh thu|doanhthu|đơn|don|bán|ban|tiền|tien)\b", normalized_question):
        has_status = any(
            any("trang_thai" in str(column).lower() or "status" in str(column).lower() for column in profile.columns)
            for profile in profiles.values()
        )
        if has_status and not re.search(r"\b(trang_thai|status)\b", sql, re.IGNORECASE):
            flags.append("NO_STATUS_FILTER")
    if re.search(r"\bjoin\b", sql, re.IGNORECASE) and re.search(r"\b(sum|avg|count)\s*\(", sql, re.IGNORECASE):
        if not re.search(r"\b(count\s*\(\s*distinct|group\s+by)\b", sql, re.IGNORECASE):
            flags.append("FANOUT_RISK")
    return flags


def retry_hint(error: str | None, sql: str, ctx: Any) -> str:
    """Build deterministic retry guidance from an execution error."""
    message = (error or "").lower()
    if "column" in message and ("not found" in message or "does not exist" in message):
        columns = [column for profile in (getattr(ctx, "tables", {}) or {}).values() for column in profile.columns]
        return f"Dùng tên cột hợp lệ gần nhất: {', '.join(columns[:20])}."
    if "table" in message and ("not found" in message or "does not exist" in message):
        return f"Chỉ dùng các bảng: {', '.join(getattr(ctx, 'tables', {}) or {})}."
    if "conversion" in message or "cast" in message:
        return "Dùng TRY_CAST và kiểm tra dữ liệu chuỗi lẫn số trước khi tính toán."
    if "parser" in message or "syntax" in message:
        return "Sửa cú pháp DuckDB, chỉ trả về một câu lệnh SELECT."
    if "empty" in message or "null" in message:
        return "Kiểm tra lại giá trị lọc, khoảng ngày và dùng TRY_CAST nếu cần."
    if "fanout" in message:
        return "Aggregate từng bảng theo khóa trước khi JOIN để tránh nhân dòng."
    return "Kiểm tra lại schema, kiểu dữ liệu và chỉ dùng SELECT DuckDB."


def validate_with_retry(
    candidate: Candidate,
    ctx: Any,
    retry: Any | None = None,
    *,
    max_retries: int = 2,
    question: str = "",
    timeout: float = 20,
    max_rows: int = 1000,
) -> Candidate:
    """Execute a candidate and optionally ask a caller-provided retry function for SQL."""
    if max_retries < 0:
        raise ValueError("max_retries must not be negative")
    validate_candidate(candidate, ctx, question=question, timeout=timeout, max_rows=max_rows)
    if candidate.rows is not None or retry is None:
        return candidate
    for _ in range(max_retries):
        new_sql = retry(candidate, retry_hint(candidate.error, candidate.sql or "", ctx))
        if not isinstance(new_sql, str) or not new_sql.strip():
            break
        candidate.sql = new_sql.strip().rstrip(";")
        candidate.error = None
        candidate.flags = [flag for flag in candidate.flags if flag not in {"UNSAFE_SQL", "PARSE_ERROR", "TIMEOUT"}]
        validate_candidate(candidate, ctx, question=question, timeout=timeout, max_rows=max_rows)
        if candidate.rows is not None:
            break
    return candidate


def run_sql(ctx: Any, sql: str, timeout: float = 20, max_rows: int = 1000) -> tuple[list[str] | None, list[list[Any]] | None, str | None]:
    """Execute one validated query with a timeout and bounded result set."""
    if timeout <= 0 or max_rows < 1:
        raise ValueError("timeout must be positive and max_rows must be positive")
    connection = getattr(ctx, "con", ctx)
    cursor = connection.cursor()
    timer = threading.Timer(timeout, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        result = cursor.execute(sql)
        description = result.description or []
        columns = [item[0] for item in description]
        rows = [list(row) for row in result.fetchmany(max_rows)]
        return columns, rows, None
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        if "interrupt" in message.lower() or "timeout" in message.lower():
            return None, None, f"TIMEOUT: {message}"
        return None, None, message
    finally:
        timer.cancel()
        try:
            cursor.close()
        except Exception:
            pass


def validate_candidate(
    candidate: Candidate,
    ctx: Any,
    *,
    question: str = "",
    timeout: float = 20,
    max_rows: int = 1000,
) -> Candidate:
    """Validate and execute a candidate in-place, returning the same candidate."""
    if candidate.answer_type == "unanswerable":
        return candidate
    if not candidate.sql:
        candidate.error = candidate.error or "Missing SQL"
        candidate.flags.append("PARSE_ERROR")
        return candidate
    errors, flags = static_check(candidate.sql, ctx)
    candidate.flags.extend(flag for flag in flags if flag not in candidate.flags)
    candidate.flags.extend(flag for flag in semantic_lint(candidate.sql, ctx, question) if flag not in candidate.flags)
    if errors:
        candidate.error = "; ".join(errors)
        candidate.flags.extend(flag for flag in ("UNSAFE_SQL",) if flag not in candidate.flags)
        return candidate
    columns, rows, error = run_sql(ctx, candidate.sql, timeout=timeout, max_rows=max_rows)
    candidate.columns, candidate.rows, candidate.error = columns, rows, error
    if error and error.startswith("TIMEOUT"):
        candidate.flags.append("TIMEOUT")
    return candidate


def validate_candidates(candidates: Iterable[Candidate], ctx: Any, **kwargs: Any) -> list[Candidate]:
    return [validate_candidate(candidate, ctx, **kwargs) for candidate in candidates]


__all__ = [
    "HARD_FLAGS",
    "retry_hint",
    "run_sql",
    "semantic_lint",
    "static_check",
    "validate_candidate",
    "validate_candidates",
    "validate_with_retry",
]
