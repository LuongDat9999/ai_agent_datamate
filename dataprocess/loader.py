"""DataMate module 1: load, clean, profile and expose a business dataset."""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import openpyxl

import pandas as pd

from .cleaner import clean_table, parse_date_cell, vn_norm
from .order_status import StatusColumn, render_status_prompt, standardize_statuses
from .profiler import (
    EntityIndex, TableProfile, build_entity_index, detect_capabilities,
    infer_table_semantics, profile_table, render_schema,
)

_LOGGER = logging.getLogger(__name__)
SUPPORTED_SUFFIXES = {".csv", ".xlsx", ".xls", ".sqlite", ".db"}
SHARD_RE = re.compile(r"^(?P<prefix>.+?)(?:_t?(?P<month>\d{1,2})_(?P<year>\d{4})|_(?P<year2>\d{4})_(?P<month2>\d{1,2}))$")
CACHE_VERSION = 1


@dataclass
class ScannedBusiness:
    data: list[Path]
    readme_text: str = ""


@dataclass
class BusinessContext:
    business_id: str
    con: Any
    tables: dict[str, TableProfile]
    readme_text: str
    entity_index: EntityIndex
    capabilities: dict[str, Any]
    schema_prompt: str
    warnings: list[str] = field(default_factory=list)
    status_columns: dict[str, StatusColumn] = field(default_factory=dict)
    status_prompt: str = ""
    cache_hit: bool = False
    coverage_safe: bool = True


def scan_business(dir_path: str | Path) -> ScannedBusiness:
    path = Path(dir_path)
    if not path.is_dir():
        raise FileNotFoundError(f"Business directory does not exist: {path}")
    data, readme = [], ""
    for item in sorted(path.iterdir(), key=lambda p: p.name.lower()):
        if item.name.lower() == "readme.md":
            readme = item.read_text(encoding="utf-8", errors="ignore")
        elif item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES:
            data.append(item)
    return ScannedBusiness(data, readme)


def _csv_delimiter(path: Path) -> str:
    sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _read_csv(path: Path) -> list[tuple[str, str | None, pd.DataFrame]]:
    errors = []
    for encoding in ("utf-8-sig", "utf-8", "cp1258"):
        try:
            frame = pd.read_csv(path, header=None, dtype=str, sep=_csv_delimiter(path), encoding=encoding)
            return [(path.name, None, frame)]
        except (UnicodeDecodeError, pd.errors.ParserError) as error:
            errors.append(str(error))
    raise ValueError(f"Cannot read CSV {path}: {'; '.join(errors)}")


def _read_excel(path: Path) -> list[tuple[str, str | None, pd.DataFrame]]:
    if path.suffix.lower() == ".xls":
        return [
            (path.name, sheet, pd.read_excel(path, sheet_name=sheet, header=None, dtype=object))
            for sheet in pd.ExcelFile(path).sheet_names
        ]
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True, read_only=False)
    result = []
    for worksheet in workbook.worksheets:
        for merged in list(worksheet.merged_cells.ranges):
            value = worksheet.cell(merged.min_row, merged.min_col).value
            worksheet.unmerge_cells(str(merged))
            for row in worksheet.iter_rows(
                min_row=merged.min_row,
                max_row=merged.max_row,
                min_col=merged.min_col,
                max_col=merged.max_col,
            ):
                for cell in row:
                    cell.value = value
        result.append((path.name, worksheet.title, pd.DataFrame(worksheet.values)))
    return result


def _read_sqlite(path: Path) -> list[tuple[str, str | None, pd.DataFrame]]:
    result = []
    with sqlite3.connect(path) as connection:
        names = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (name,) in names:
            result.append((path.name, name, pd.read_sql_query(f'SELECT * FROM "{name}"', connection)))
    return result


def _read_source(path: Path) -> list[tuple[str, str | None, pd.DataFrame]]:
    if path.suffix.lower() == ".csv":
        return _read_csv(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return _read_excel(path)
    return _read_sqlite(path)


def _safe_table_name(value: str, used: set[str]) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower() or "table"
    if name[0].isdigit():
        name = f"t_{name}"
    candidate, suffix = name, 2
    while candidate in used:
        candidate, suffix = f"{name}_{suffix}", suffix + 1
    used.add(candidate)
    return candidate


def _date_columns(frame: pd.DataFrame) -> list[str]:
    return [
        str(column) for column in frame.columns
        if any(token in vn_norm(column) for token in ("ngay", "date", "time", "thoi_gian"))
    ]


def _parsed_date_series(series: pd.Series) -> tuple[pd.Series, str | None, float]:
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(series, errors="coerce").dt.normalize()
        return parsed, None, float(parsed.notna().mean())
    values = series.dropna()
    if not len(values):
        return pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]"), None, 0.0
    best = (pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]"), None, 0.0)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        parsed = pd.to_datetime(series, format=fmt, errors="coerce")
        ratio = float(parsed.notna().sum() / len(values))
        if ratio > best[2]:
            best = (parsed.dt.normalize(), fmt, ratio)
    def safe_parse(value: Any) -> Any:
        try:
            return parse_date_cell(value)
        except (TypeError, ValueError, OverflowError):
            return None

    parsed = series.map(safe_parse)
    ratio = float(parsed.notna().sum() / len(values))
    if ratio > best[2]:
        best = (pd.to_datetime(parsed, errors="coerce").dt.normalize(), None, ratio)
    return best


def _date_source(frame: pd.DataFrame) -> tuple[str | None, str | None, float]:
    named = set(_date_columns(frame))
    typed = [
        str(column) for column in frame.columns
        if pd.api.types.is_datetime64_any_dtype(frame[column])
    ]
    textual = [
        str(column) for column in frame.columns
        if pd.api.types.is_object_dtype(frame[column]) or pd.api.types.is_string_dtype(frame[column])
    ]
    candidates = list(dict.fromkeys(_date_columns(frame) + typed + textual + (["ngay"] if "ngay" in frame else [])))
    best: tuple[str | None, str | None, float] = (None, None, 0.0)
    for column in candidates:
        explicit = column in named or column in typed or column == "ngay"
        if not explicit:
            values = frame[column].dropna()
            if not len(values):
                continue
            ratios = [
                float(pd.to_datetime(values, format=fmt, errors="coerce").notna().mean())
                for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")
            ]
            ratio = max(ratios)
            if ratio < 0.95:
                continue
            date_format = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")[ratios.index(ratio)]
        else:
            _, date_format, ratio = _parsed_date_series(frame[column])
        priority = 1.0 if pd.api.types.is_datetime64_any_dtype(frame[column]) else 0.0
        current_priority = 1.0 if best[0] and pd.api.types.is_datetime64_any_dtype(frame[best[0]]) else 0.0
        if best[0] is None or ratio > best[2] or (ratio == best[2] and priority > current_priority):
            best = (column, date_format, ratio)
    return best


def _ensure_date_column(frame: pd.DataFrame, table_name: str = "<unknown>") -> pd.DataFrame:
    result = frame.copy()
    date_column, _, ratio = _date_source(result)
    if not date_column:
        return result
    parsed, _, parsed_ratio = _parsed_date_series(result[date_column])
    if parsed_ratio < 0.95:
        _LOGGER.warning("Date parse success below 95%% for %s.%s: %.1f%%", table_name, date_column, parsed_ratio * 100)
    result["ngay"] = parsed
    result.attrs["date_parse_warning"] = parsed_ratio < 0.95
    return result


def _public_columns(frame: pd.DataFrame) -> list[str]:
    result = []
    for column in frame.columns:
        name = str(column)
        if name == "is_total":
            continue
        if name != "ngay" and (name.endswith("_norm") or name.endswith("_raw")):
            continue
        if name != "ngay" and any(token in vn_norm(name) for token in ("ngay", "date", "time", "thoi_gian")):
            continue
        result.append(name)
    return result


def _select_projection(frame: pd.DataFrame, raw_name: str, source_name: str | None = None) -> str:
    columns = _public_columns(frame)
    expressions = []
    date_column, date_format, ratio = _date_source(frame)
    if date_column:
        expressions.append('CAST("ngay" AS DATE) AS "ngay"')
    expressions.extend(f'"{column}"' for column in columns if column != "ngay")
    if source_name:
        expressions.append(f"'{source_name.replace(chr(39), chr(39) * 2)}' AS \"_nguon_bang\"")
    return ", ".join(expressions)


def _connect_duckdb(
    raw_tables: dict[str, pd.DataFrame],
    status_columns: dict[str, StatusColumn] | None = None,
    public_tables: dict[str, pd.DataFrame] | None = None,
    shard_groups: dict[str, list[str]] | None = None,
    db_path: str | Path = ":memory:",
) -> Any:
    import duckdb

    con = duckdb.connect(str(db_path))
    con.execute("SET enable_external_access=false")
    con.execute("SET threads=4")
    shard_groups = shard_groups or {}
    raw_tables = {name: _ensure_date_column(frame, name) for name, frame in raw_tables.items()}
    public_tables = {
        name: _ensure_date_column(frame, name) for name, frame in (public_tables or raw_tables).items()
    }
    for name, frame in raw_tables.items():
        con.register(f"_raw_{name}", frame)
        con.execute(f'CREATE TABLE "{name}_raw" AS SELECT * FROM "_raw_{name}"')
        con.unregister(f"_raw_{name}")
    for name, frame in public_tables.items():
        members = shard_groups.get(name, [name])
        if any("is_total" in raw_tables[member].columns for member in members):
            total_filter = " WHERE NOT COALESCE(is_total, FALSE)"
        else:
            total_filter = ""
        selects = [
            f'SELECT {_select_projection(raw_tables[member], member, member if len(members) > 1 else None)} '
            f'FROM "{member}_raw"{total_filter}'
            for member in members
        ]
        union_sql = " UNION ALL BY NAME ".join(selects)
        con.execute(f'CREATE VIEW "{name}" AS {union_sql}')
        if status_columns and (name in status_columns or any(member in status_columns for member in members)):
            con.execute(f'CREATE VIEW "{name}_hop_le" AS SELECT * FROM "{name}" WHERE trang_thai_nhom = \'hop_le\'')
            con.execute(f'CREATE VIEW "{name}_khong_huy" AS SELECT * FROM "{name}" WHERE trang_thai_chuan <> \'huy\'')
        semantics = infer_table_semantics(frame)
        key = semantics.get("order_key")
        if key:
            line_name = f"_line_{name}"
            order_name = f"_order_{name}"
            valid_filter = ""
            if status_columns and name in status_columns and "trang_thai_nhom" in frame.columns:
                valid_filter += " AND trang_thai_nhom = 'hop_le'"
            con.execute(f'CREATE VIEW "{line_name}" AS SELECT * FROM "{name}" WHERE TRUE{valid_filter}')
            numeric = [column for column in frame.columns if pd.api.types.is_numeric_dtype(frame[column]) and column != key]
            aggregates = [f'SUM("{column}") AS "{column}"' for column in numeric if column != "is_total"]
            if not aggregates:
                aggregates = [f'COUNT(*) AS row_count']
            con.execute(
                f'CREATE VIEW "{order_name}" AS SELECT "{key}", {", ".join(aggregates)} '
                f'FROM "{line_name}" GROUP BY "{key}"'
            )
    return con


def _merge_compatible_tables(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    groups: dict[tuple[str, ...], list[tuple[str, pd.DataFrame]]] = {}
    for name, frame in tables.items():
        signature = tuple(sorted(column for column in frame.columns if column != "_nguon_sheet"))
        groups.setdefault(signature, []).append((name, frame))
    merged = dict(tables)
    master_index = 1
    for members in groups.values():
        if len(members) < 2:
            continue
        combined = pd.concat([frame for _, frame in members], ignore_index=True, sort=False)
        merged[f"master_{master_index}"] = combined
        master_index += 1
    return merged


def _shard_groups(tables: dict[str, pd.DataFrame]) -> dict[str, list[str]]:
    candidates: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for name, frame in tables.items():
        match = SHARD_RE.match(name)
        if match:
            prefix = match.group("prefix")
            signature = tuple(sorted(str(column) for column in frame.columns))
            candidates.setdefault((prefix, signature), []).append(name)
    return {prefix: sorted(members) for (prefix, _), members in candidates.items() if len(members) > 1}


def _profile_frame(frame: pd.DataFrame, members: list[str] | None = None) -> pd.DataFrame:
    result = _ensure_date_column(frame)
    date_column, _, _ = _date_source(result)
    if date_column and date_column != "ngay":
        result["ngay"] = pd.to_datetime(result[date_column], errors="coerce", dayfirst=True).dt.normalize()
    if "ngay" in result:
        result["ngay"] = pd.to_datetime(result["ngay"], errors="coerce").dt.normalize()
    keep = _public_columns(result)
    if "ngay" in result and "ngay" not in keep:
        keep.insert(0, "ngay")
    result = result[keep].copy()
    return result


def _missing_months(frame: pd.DataFrame) -> list[str]:
    if "ngay" not in frame or not frame["ngay"].notna().any():
        return []
    values = pd.to_datetime(frame["ngay"], errors="coerce").dropna()
    present = {(value.year, value.month) for value in values}
    start, end = values.min().to_period("M"), values.max().to_period("M")
    return [str(period) for period in pd.period_range(start, end, freq="M") if (period.year, period.month) not in present]


def _cache_paths(dir_path: Path, business_id: str) -> tuple[Path, Path]:
    del dir_path
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", business_id)
    cache_dir = Path("outputs/cache")
    return cache_dir / f"db_{safe_id}.duckdb", cache_dir / f"db_{safe_id}.json"


def _source_fingerprint(sources: list[Path]) -> list[dict[str, Any]]:
    return [
        {"name": str(path), "mtime_ns": path.stat().st_mtime_ns, "size": path.stat().st_size}
        for path in sources
    ]


def _cached_context(
    cache_db: Path,
    cache_meta: Path,
    expected_fingerprint: list[dict[str, Any]],
    business_id: str,
) -> BusinessContext | None:
    if not cache_db.is_file() or not cache_meta.is_file():
        return None
    try:
        metadata = json.loads(cache_meta.read_text(encoding="utf-8"))
        if (
            metadata.get("cache_version") != CACHE_VERSION
            or metadata.get("fingerprint") != expected_fingerprint
            or metadata.get("business_id") != business_id
        ):
            return None
        profiles = {
            name: TableProfile(**profile)
            for name, profile in metadata["profiles"].items()
        }
        status_columns = {
            name: StatusColumn(**status)
            for name, status in metadata.get("status_columns", {}).items()
        }
        entity_index = EntityIndex(**metadata["entity_index"])
        import duckdb

        con = duckdb.connect(str(cache_db))
        con.execute("SET enable_external_access=false")
        return BusinessContext(
            business_id=business_id,
            con=con,
            tables=profiles,
            readme_text=metadata.get("readme_text", ""),
            entity_index=entity_index,
            capabilities=metadata["capabilities"],
            schema_prompt=metadata["schema_prompt"],
            warnings=metadata.get("warnings", []),
            status_columns=status_columns,
            status_prompt=metadata.get("status_prompt", ""),
            cache_hit=True,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        _LOGGER.warning("Ignoring invalid cache metadata for business %s", business_id, exc_info=True)
        return None


def _write_cache_metadata(
    cache_meta: Path,
    context: BusinessContext,
    fingerprint: list[dict[str, Any]],
) -> None:
    metadata = {
        "cache_version": CACHE_VERSION,
        "business_id": context.business_id,
        "fingerprint": fingerprint,
        "tables": sorted(context.tables),
        "profiles": {name: asdict(profile) for name, profile in context.tables.items()},
        "capabilities": context.capabilities,
        "entity_index": asdict(context.entity_index),
        "schema_prompt": context.schema_prompt,
        "readme_text": context.readme_text,
        "warnings": context.warnings,
        "status_columns": {name: asdict(status) for name, status in context.status_columns.items()},
        "status_prompt": context.status_prompt,
    }
    cache_meta.write_text(json.dumps(metadata, ensure_ascii=False, default=str), encoding="utf-8")


def load_business(
    dir_path: str | Path,
    business_id: str | None = None,
    use_cache: bool | None = None,
) -> BusinessContext:
    dir_path = Path(dir_path)
    resolved_business_id = business_id or dir_path.name
    scanned = scan_business(dir_path)
    if use_cache is None:
        use_cache = os.environ.get("USE_DB_CACHE", "1").strip().lower() not in {"0", "false", "no", "off"}
    cache_db, cache_meta = _cache_paths(dir_path, resolved_business_id)
    fingerprint = _source_fingerprint(scanned.data)
    if use_cache:
        cached = _cached_context(cache_db, cache_meta, fingerprint, resolved_business_id)
        if cached is not None:
            return cached
        cache_db.parent.mkdir(parents=True, exist_ok=True)
        if cache_db.exists():
            cache_db.unlink()
    rulebook_path = Path(dir_path).parent.parent / "rulebook.md"
    rulebook = rulebook_path.read_text(encoding="utf-8") if rulebook_path.exists() else ""
    cleaned, used_names = {}, set()
    for source in scanned.data:
        for file_name, sheet, raw in _read_source(source):
            source_label = f"{file_name}.{sheet}" if sheet else file_name
            if raw.empty:
                _LOGGER.warning("Skipping empty source %s", source_label)
                continue
            name_source = f"{Path(file_name).stem}_{sheet}" if sheet else Path(file_name).stem
            table = clean_table(raw, file_name, sheet)
            if not table.empty:
                table_name = _safe_table_name(name_source, used_names)
                cleaned[table_name] = table
                _LOGGER.info(
                    "Loaded source %s as %s: raw_rows=%d cleaned_rows=%d columns=%s",
                    source_label,
                    table_name,
                    len(raw),
                    len(table),
                    list(table.columns),
                )
            else:
                _LOGGER.warning("Skipping source %s after cleaning: no rows", source_label)
    if not cleaned:
        raise ValueError(f"No readable data tables found in {dir_path}")
    status_columns, cleaned = standardize_statuses(
        cleaned,
        rulebook=rulebook,
        business_id=business_id or Path(dir_path).name,
    )
    coverage_safe = not any(
        bool(frame.attrs.get("date_parse_warning")) for frame in cleaned.values()
    )
    groups = _shard_groups(cleaned)
    shard_members = {member for members in groups.values() for member in members}
    profile_tables = {name: frame for name, frame in cleaned.items() if name not in shard_members}
    public_status_columns = dict(status_columns)
    merge_warnings = []
    for name, members in groups.items():
        combined = pd.concat(
            [cleaned[member].assign(_nguon_bang=member) for member in members],
            ignore_index=True,
            sort=False,
        )
        profile_tables[name] = _profile_frame(combined)
        if "ngay" in profile_tables[name] and profile_tables[name]["ngay"].notna().any():
            dates = pd.to_datetime(profile_tables[name]["ngay"], errors="coerce").dropna()
            missing = _missing_months(profile_tables[name])
            merge_warnings.append(
                f"VIEW {name}: shards={len(members)}, range={dates.min().date()}.."
                f"{dates.max().date()}, missing_months={missing}"
            )
        status = next((status_columns[member] for member in members if member in status_columns), None)
        if status is not None:
            public_status_columns[name] = status
    profile_tables = {name: _profile_frame(frame) for name, frame in profile_tables.items()}
    profiles = {name: profile_table(name, frame) for name, frame in profile_tables.items()}
    for name, frame in profile_tables.items():
        semantics = infer_table_semantics(frame)
        profiles[name].grain = semantics["grain"]
        profiles[name].grain_confidence = semantics["grain_confidence"]
        profiles[name].roles = semantics["roles"]
        profiles[name].measures = semantics["measures"]
        profiles[name].semantic_notes.append(
            "Use _line_<table> for row-level measures and _order_<table> for one-row-per-order aggregates."
        )
        key = semantics.get("order_key")
        if key:
            line_name = f"_line_{name}"
            order_name = f"_order_{name}"
            line_frame = frame.drop(columns=["is_total"], errors="ignore")
            if name in status_columns and "trang_thai_nhom" in line_frame:
                line_frame = line_frame[line_frame["trang_thai_nhom"] == "hop_le"]
            line_profile = profile_table(line_name, line_frame)
            line_profile.grain = "line"
            line_profile.grain_confidence = semantics["grain_confidence"]
            line_profile.roles = semantics["roles"]
            line_profile.measures = semantics["measures"]
            line_profile.semantic_notes = ["Generated view: total rows removed and valid-status rows retained."]
            profiles[line_name] = line_profile
            order_columns = [key] + [
                column for column in frame.columns
                if column != key and pd.api.types.is_numeric_dtype(frame[column]) and column != "is_total"
            ]
            order_frame = pd.DataFrame(columns=order_columns)
            order_profile = profile_table(order_name, order_frame)
            order_profile.grain = "order"
            order_profile.grain_confidence = semantics["grain_confidence"]
            order_profile.roles = semantics["roles"]
            order_profile.measures = semantics["measures"]
            order_profile.semantic_notes = ["Generated view: one row per inferred order key."]
            profiles[order_name] = order_profile
    for name in public_status_columns:
        frame = profile_tables.get(name)
        if frame is None or "trang_thai_chuan" not in frame:
            continue
        for suffix, predicate in (("_hop_le", lambda values: values.isin({"hoan_thanh", "dang_giao"})),
                                  ("_khong_huy", lambda values: values != "huy")):
            view_frame = frame[predicate(frame["trang_thai_chuan"])]
            profiles[name + suffix] = profile_table(name + suffix, view_frame)
    context = BusinessContext(
        business_id=resolved_business_id,
        con=_connect_duckdb(
            cleaned,
            public_status_columns,
            profile_tables,
            groups,
            db_path=cache_db if use_cache else ":memory:",
        ),
        tables=profiles,
        readme_text=scanned.readme_text,
        entity_index=build_entity_index(profile_tables),
        capabilities=detect_capabilities(profile_tables),
        schema_prompt=render_schema(profiles),
        warnings=merge_warnings,
        status_columns=public_status_columns,
        status_prompt=render_status_prompt(public_status_columns, profile_tables),
        coverage_safe=coverage_safe,
    )
    if use_cache:
        _write_cache_metadata(cache_meta, context, fingerprint)
    return context

def read_excel_grid(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    grids = {}
    for ws in wb.worksheets:
        for rng in list(ws.merged_cells.ranges):
            top_left = ws.cell(rng.min_row, rng.min_col).value
            ws.unmerge_cells(str(rng))
            for row in ws.iter_rows(min_row=rng.min_row, max_row=rng.max_row,
                                    min_col=rng.min_col, max_col=rng.max_col):
                for c in row:
                    c.value = top_left
        grids[ws.title] = pd.DataFrame(ws.values)      # header=None
    return grids