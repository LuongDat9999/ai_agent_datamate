"""Prompt construction for the DataMate question-to-SQL stage."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .abbrev import expand_abbrev
from .date_resolver import resolve_time
from .entity_matcher import EntityHint, format_entity_hints, match_entities
from .token_budget import prompt_budget, prompt_margin, prompt_token_count

SYSTEM_PROMPT = """[VAI TRÒ]
Bạn là chuyên gia phân tích dữ liệu cho DataMate. Hãy sinh một truy vấn SELECT DuckDB
để trả lời chính xác câu hỏi tiếng Việt trên dữ liệu doanh nghiệp.

[AN TOÀN]
Chỉ dùng bảng và cột có trong schema. Không đọc file, không gọi mạng, không dùng DDL/DML,
read_csv/read_parquet/read_json, attach, install, load hoặc pragma. Không bịa dữ liệu.
Nếu schema/capabilities không hỗ trợ câu hỏi, trả type unanswerable.

[DUCKDB]
Dùng TRY_CAST cho dữ liệu bẩn, ILIKE/vn_norm cho tên không dấu, date_trunc và strftime
cho ngày. Percent phải nhân 100. Aggregate trước khi JOIN để tránh fan-out. Ranking
phải có tie-breaker ổn định. Phân biệt doanh thu, tiền đã thu, tiền phải trả, số đơn
và số dòng hàng.

[ĐẦU RA]
<thinking>Tối đa 5 dòng: bảng, cột, bộ lọc, đơn vị, thứ tự.</thinking>
<type>number|percent|string|date|boolean|list|table|unanswerable</type>
<sql>
SELECT ...
</sql>
Với unanswerable, bỏ thẻ sql và ghi <reason>...</reason>.
"""


@dataclass
class QuestionContext:
    question: str
    as_of_date: str
    periods: dict[str, Any] = field(default_factory=dict)
    entity_hints: list[EntityHint] = field(default_factory=list)
    fewshots: list[dict[str, Any]] = field(default_factory=list)
    local_rules: str = ""
    normalized_question: str = ""
    warnings: list[str] = field(default_factory=list)
    prompt_tokens_estimate: int = 0
    prompt_reduction_steps: list[str] = field(default_factory=list)
    prompt_part_tokens: dict[str, int] = field(default_factory=dict)
    prompt_over_budget: bool = False


def _value(obj: Any, name: str, default: Any = "") -> Any:
    return getattr(obj, name, default) if obj is not None else default


def _schema_for_question(schema: str, question: str, hints: Iterable[EntityHint], max_chars: int) -> tuple[str, bool]:
    if len(schema) <= max_chars:
        return schema, False
    terms = set(re.findall(r"[a-z0-9_]{3,}", question.casefold()))
    tables = {hint.table.casefold() for hint in hints}
    selected, current = [], []
    keep = False
    for line in schema.splitlines():
        if line.startswith("TABLE "):
            if keep:
                selected.extend(current)
            current = [line]
            keep = any(table in line.casefold() for table in tables) or any(term in line.casefold() for term in terms)
        else:
            current.append(line)
            keep = keep or any(term in line.casefold() for term in terms)
    if keep:
        selected.extend(current)
    compact = "\n".join(selected)
    if len(compact) > max_chars:
        compact = compact[:max_chars].rsplit("\n", 1)[0]
    return compact or schema[:max_chars], True


def _fewshot_text(fewshots: Iterable[Mapping[str, Any]]) -> str:
    blocks = []
    for item in fewshots:
        blocks.append(
            "[VÍ DỤ THAM KHẢO]\n"
            f"Câu hỏi: {item.get('question', '')}\n"
            f"Type: {item.get('type', '')}\n"
            f"SQL:\n{item.get('sql', '')}\n"
            "Không sao chép tên bảng/cột nếu không có trong schema."
        )
    return "\n\n".join(blocks) or "(không có)"


def _estimate_tokens(text: str) -> int:
    return prompt_token_count(text)


def _compact_schema(schema: str, *, drop_raw: bool = False, related_only: bool = False,
                    question: str = "", hints: Iterable[EntityHint] = ()) -> str:
    terms = set(re.findall(r"[a-z0-9_]{3,}", question.casefold()))
    tables = {hint.table.casefold() for hint in hints}
    out, current, keep = [], [], True
    for line in schema.splitlines():
        if line.startswith("TABLE "):
            if current and keep:
                out.extend(current)
            current = [line]
            table_name = line.split()[1].casefold()
            keep = not related_only or table_name in tables or any(term in line.casefold() for term in terms)
        else:
            if drop_raw and re.search(r"\b\w+_(?:raw|goc)\b", line.split(":", 1)[0]):
                continue
            distinct = re.search(r"distinct=(\d+)", line)
            if distinct and int(distinct.group(1)) > 10:
                line = re.sub(r", values=\[[^\]]*\]", "", line)
            else:
                def trim_values(match: re.Match[str]) -> str:
                    values = match.group(1).split(", ")
                    return ", values=[" + ", ".join(values[:10]) + "]"
                line = re.sub(r", values=\[([^\]]*)\]", trim_values, line)
            current.append(line)
    if current and keep:
        out.extend(current)
    return "\n".join(out)


def _rulebook_core() -> str:
    path = Path(__file__).with_name("rulebook_core.md")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    kept = []
    for line in text.splitlines():
        if re.match(r"\s*\[QT(?:0|1\.1|1\.3|2(?:\.[^ ]+)?|8(?:\.[^ ]+)?)\]", line):
            continue
        if re.match(r"\s*\[QT1\.4\]", line):
            kept.append("Dùng hướng dẫn chuẩn hoá trạng thái và các view được cung cấp trong schema.")
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _industry_section(rulebook: str) -> str:
    match = re.search(r"(?ms)^## 6\. Theo ngành\b.*?(?=^##\s|\Z)", rulebook)
    return match.group(0).strip() if match else "(không có mục 6)"


def _compact_readme(readme: str, max_lines: int = 80) -> str:
    lines = readme.splitlines()
    if len(lines) <= max_lines:
        return readme
    keep_words = ("quy đổi", "đơn vị", "ghi chú", "nghĩa là", "tương đương", "trạng thái", "không phải")
    kept = [line for line in lines if any(word in line.casefold() for word in keep_words)]
    return "\n".join(kept[:max_lines]) or "\n".join(lines[:max_lines])


def _minimal_schema(schema: str) -> str:
    """Keep every table and header while dropping descriptions and sample values."""
    lines: list[str] = []
    for line in schema.splitlines():
        if line.startswith("TABLE "):
            lines.append(line)
        elif line.lstrip().startswith("- "):
            column = line.split(":", 1)[0].strip()
            lines.append(f"  {column}")
    return "\n".join(lines)


def build_prompt(
    question: str,
    as_of_date: str,
    business_context: Any,
    rulebook: str = "",
    fewshots: Iterable[Mapping[str, Any]] = (),
    max_schema_chars: int = 16000,
    calendar: Any = None,
) -> tuple[QuestionContext, str]:
    if not question.strip():
        raise ValueError("question must not be empty")
    expanded = expand_abbrev(question)
    resolved = resolve_time(question, as_of_date, calendar=calendar, rulebook=rulebook)
    hints = match_entities(question, _value(business_context, "entity_index", {}))
    schema, truncated = _schema_for_question(_value(business_context, "schema_prompt", ""), question, hints, max_schema_chars)
    warnings = list(resolved["warnings"])
    if truncated:
        warnings.append("SCHEMA_TRUNCATED")
    context = QuestionContext(
        question=question,
        as_of_date=as_of_date,
        periods=resolved["periods"],
        entity_hints=hints,
        fewshots=list(fewshots),
        local_rules=rulebook,
        normalized_question=expanded["normalized_question"],
        warnings=warnings,
    )
    capabilities = _value(business_context, "capabilities", {})
    readme = _value(business_context, "readme_text", "")[:6000]
    core_rules = _rulebook_core()
    industry_rules = _industry_section(rulebook)
    status_rules = _value(business_context, "status_prompt", "")
    max_tokens = prompt_budget() - prompt_margin()
    if max_tokens < 1:
        raise ValueError("MAX_PROMPT_TOKENS must be greater than PROMPT_TOKEN_MARGIN")
    steps: list[str] = []
    sample_rows = "\n".join(
        f"{name}: {profile.sample_rows}" for name, profile in _value(business_context, "tables", {}).items()
        if getattr(profile, "sample_rows", None)
    )
    schema_options = schema
    fewshot_options = context.fewshots

    def render() -> str:
        return f"""{SYSTEM_PROMPT}
[LUẬT CÒN LẠI]
{core_rules or "(không có)"}

[THEO NGÀNH]
{industry_rules}

[TRẠNG THÁI]
{status_rules or "Không có cột trạng thái được phát hiện."}

[DOANH NGHIỆP]
business_id: {_value(business_context, "business_id", "")}
as_of_date: {as_of_date}
capabilities: {capabilities}
README:
{readme or "(không có)"}

[SCHEMA DUCKDB]
{schema_options or "(schema rỗng - không đủ dữ liệu để trả lời)"}

[SAMPLE ROWS]
{sample_rows or "(không có)"}

[VÍ DỤ TƯƠNG TỰ]
{_fewshot_text(fewshot_options)}

[KHOẢNG THỜI GIAN ĐÃ GIẢI MÃ]
{context.periods}
warnings: {context.warnings}

[GỢI Ý THỰC THỂ]
{format_entity_hints(hints)}

[CÂU HỎI GỐC]
{question}
"""

    def over_budget() -> bool:
        return _estimate_tokens(render()) > max_tokens

    if over_budget():
        schema_options = _compact_schema(schema_options)
        steps.append("limit_distinct_values")
    if over_budget():
        sample_rows = "\n".join(
            f"{name}: {profile.sample_rows[:2]}" for name, profile in _value(business_context, "tables", {}).items()
            if getattr(profile, "sample_rows", None)
        )
        steps.append("limit_sample_rows")
    if over_budget():
        schema_options = _compact_schema(schema_options, drop_raw=True)
        steps.append("drop_raw_columns")
    if over_budget():
        readme = _compact_readme(readme)
        steps.append("compact_readme")
    if over_budget():
        sample_rows = ""
        fewshot_options = []
        steps.append("remove_optional_examples")
    if over_budget():
        schema_options = _minimal_schema(schema_options)
        steps.append("keep_schema_headers_only")
    if over_budget():
        readme = _compact_readme(readme, max_lines=20)
        steps.append("compact_readme_semantic_lines")
    prompt = render()
    context.prompt_tokens_estimate = _estimate_tokens(prompt)
    context.prompt_reduction_steps = steps
    context.prompt_over_budget = context.prompt_tokens_estimate > max_tokens
    if context.prompt_over_budget:
        context.warnings.append("PROMPT_OVER_BUDGET")
    context.prompt_part_tokens = {
        "luat": _estimate_tokens(core_rules),
        "muc_6": _estimate_tokens(industry_rules),
        "readme": _estimate_tokens(readme),
        "schema": _estimate_tokens(schema_options),
        "hint": _estimate_tokens(format_entity_hints(hints)),
        "question": _estimate_tokens(question),
    }
    return context, prompt


def build_retry_prompt(base_prompt: str, failed_sql: str, error: str, hints: str = "") -> str:
    return f"""{base_prompt}

[RETRY]
SQL trước đó:
{failed_sql}

Lỗi validator/DuckDB:
{error}

Gợi ý đã tính bằng code:
{hints or "(không có)"}

Hãy sửa đúng lỗi trên, vẫn chỉ dùng SELECT và schema đã cung cấp. Xuất lại đầy đủ contract.
"""


def prompt_metadata(prompt: str, context: QuestionContext) -> dict[str, Any]:
    return {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "question_length": len(context.question),
        "warning_count": len(context.warnings),
        "hint_count": len(context.entity_hints),
        "fewshot_count": len(context.fewshots),
        "prompt_tokens_estimate": context.prompt_tokens_estimate,
        "prompt_reduction_steps": context.prompt_reduction_steps,
        "prompt_part_tokens": context.prompt_part_tokens,
        "prompt_over_budget": context.prompt_over_budget,
    }


__all__ = ["QuestionContext", "SYSTEM_PROMPT", "build_prompt", "build_retry_prompt", "prompt_metadata"]
