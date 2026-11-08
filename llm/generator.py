"""Module 3: generate and parse Question -> SQL candidates.

The generator deliberately knows nothing about a concrete model runtime.  It
only talks to the backend contract in :mod:`llm_backend`, so switching between
the mock, OpenAI-compatible and vLLM backends is an environment-only change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .llm_backend import get_backend

ANSWER_TYPES = frozenset(
    {"number", "percent", "string", "date", "boolean", "list", "table", "unanswerable"}
)
_SQL_BLOCK = re.compile(r"<sql>\s*(.*?)\s*</sql>", re.IGNORECASE | re.DOTALL)
_TYPE_TAG = re.compile(r"<type>\s*([^<]+?)\s*</type>", re.IGNORECASE | re.DOTALL)
_REASON_TAG = re.compile(r"<reason>\s*(.*?)\s*</reason>", re.IGNORECASE | re.DOTALL)
_FENCED_SQL = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_FORBIDDEN_SQL = re.compile(
    r"\b(?:attach|copy|create|delete|drop|export|install|insert|load|pragma|"
    r"read_csv|read_json|read_parquet|reset|set|update|use)\b",
    re.IGNORECASE,
)


@dataclass
class Candidate:
    """A parsed model response passed to Module 4."""

    sql: str | None
    answer_type: str
    rows: list[Any] | None = None
    columns: list[str] | None = None
    error: str | None = None
    flags: list[str] = field(default_factory=list)
    canon: str | None = None
    reason: str | None = None
    raw: str | None = None


def _clean_sql(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _parse_details(text: str) -> tuple[str | None, str | None, str | None]:
    type_match = _TYPE_TAG.search(text)
    sql_match = _SQL_BLOCK.search(text) or _FENCED_SQL.search(text)
    reason_match = _REASON_TAG.search(text)
    answer_type = type_match.group(1).strip().lower() if type_match else None
    sql = _clean_sql(sql_match.group(1)) if sql_match else None
    reason = reason_match.group(1).strip() if reason_match else None
    return answer_type, sql, reason


def parse_response(text: str) -> tuple[str | None, str | None]:
    """Parse the stable response contract as ``(answer_type, sql)``.

    The function is intentionally tolerant of case and fenced SQL, but does
    not invent missing fields.  Malformed responses become invalid candidates
    instead of silently turning into a successful answer.
    """

    if not isinstance(text, str):
        return None, None
    answer_type, sql, _ = _parse_details(text)
    return answer_type, sql


def _candidate_from_response(text: str) -> Candidate:
    if not isinstance(text, str) or not text.strip():
        return Candidate(None, "invalid", error="Empty LLM response", flags=["PARSE_ERROR"], raw=text)

    answer_type, sql, reason = _parse_details(text)
    if answer_type not in ANSWER_TYPES:
        return Candidate(
            sql,
            answer_type or "invalid",
            error="Missing or invalid <type> tag",
            flags=["PARSE_ERROR"],
            reason=reason,
            raw=text,
        )
    if answer_type == "unanswerable":
        if sql:
            return Candidate(
                sql,
                answer_type,
                error="unanswerable response must not contain SQL",
                flags=["PARSE_ERROR"],
                reason=reason,
                raw=text,
            )
        return Candidate(None, answer_type, reason=reason, raw=text)
    if not sql:
        return Candidate(
            None,
            answer_type,
            error="Missing <sql> tag",
            flags=["PARSE_ERROR"],
            reason=reason,
            raw=text,
        )

    flags: list[str] = []
    if _FORBIDDEN_SQL.search(sql) or not re.match(r"^(?:select|with)\b", sql, re.IGNORECASE):
        flags.append("UNSAFE_SQL")
    if sql.count(";"):
        flags.append("MULTI_STATEMENT")
    return Candidate(sql, answer_type, flags=flags, reason=reason, raw=text)


def _messages(prompt_or_messages: str | Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    if isinstance(prompt_or_messages, str):
        return [{"role": "user", "content": prompt_or_messages}]
    result = []
    for message in prompt_or_messages:
        role, content = message.get("role"), message.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise TypeError("Each message must contain string role and content")
        result.append({"role": role, "content": content})
    if not result:
        raise ValueError("messages must not be empty")
    return result


def _generate(backend: Any, messages: list[dict[str, str]], count: int, temperature: float, seed: int | None) -> list[str]:
    outputs = backend.generate(
        messages, n=count, temperature=temperature, max_tokens=256, seed=seed
    )
    if not isinstance(outputs, list):
        raise TypeError("LLM backend generate() must return list[str]")
    return [output for output in outputs if isinstance(output, str)]


def generate_candidates(
    prompt_or_messages: str | Sequence[Mapping[str, str]],
    k: int = 3,
    *,
    seed: int | None = 1234,
    backend: Any | None = None,
) -> list[Candidate]:
    """Generate up to ``k`` parsed candidates using the configured backend.

    The first candidate is deterministic; additional candidates use a small
    amount of sampling diversity for self-consistency voting.  Backend errors
    are not swallowed: an unavailable model is an operational failure, not a
    valid abstention.
    """

    if not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    messages = _messages(prompt_or_messages)
    model = backend or get_backend()
    outputs = _generate(model, messages, 1, 0.0, seed)
    if k > 1:
        outputs.extend(_generate(model, messages, k - 1, 0.4, seed))
    return [_candidate_from_response(output) for output in outputs[:k]]


def generate(
    prompt_or_messages: str | Sequence[Mapping[str, str]],
    k: int = 3,
    *,
    seed: int | None = 1234,
    backend: Any | None = None,
) -> list[Candidate]:
    """Compatibility alias matching the Module 3 terminology."""

    return generate_candidates(prompt_or_messages, k, seed=seed, backend=backend)


__all__ = ["ANSWER_TYPES", "Candidate", "generate", "generate_candidates", "parse_response"]
