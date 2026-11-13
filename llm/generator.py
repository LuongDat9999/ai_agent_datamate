"""Module 3: generate and parse Question -> SQL candidates.

The generator deliberately knows nothing about a concrete model runtime.  It
only talks to the backend contract in :mod:`llm_backend`, so switching between
the mock, OpenAI-compatible and vLLM backends is an environment-only change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..formatting.type_inference import infer_question_type
from .llm_backend import get_backend
from ..tool.token_budget import reduce_prompt

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
_LAST_GENERATION_STATS: dict[str, Any] = {}
_LAST_GENERATION_OOM = False


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
    abstain_reason: str | None = None
    declared_type: str | None = None
    inferred_type: str | None = None
    type_source: str = "none"


def _clean_sql(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _parse_details(text: str) -> tuple[str | None, str | None, str | None]:
    type_match = _TYPE_TAG.search(text)
    sql_match = _SQL_BLOCK.search(text)
    if not sql_match:
        for fence in re.finditer(r"```([^\n`]*)\n?(.*?)(?:```|$)", text, re.IGNORECASE | re.DOTALL):
            body = fence.group(2)
            if re.search(r"\b(?:select|with)\b", body, re.IGNORECASE):
                sql_match = fence
                break
    reason_match = _REASON_TAG.search(text)
    answer_type = type_match.group(1).strip().lower() if type_match else None
    if sql_match:
        body = sql_match.group(1) if sql_match.re is _SQL_BLOCK else sql_match.group(2)
        sql = _clean_sql(body)
    else:
        fallback = re.search(r"\b(?:select|with)\b[\s\S]*", text, re.IGNORECASE)
        if fallback:
            tail = fallback.group(0).split("```", 1)[0]
            statement_end = tail.find(";")
            sql = _clean_sql(tail[:statement_end + 1] if statement_end >= 0 else tail)
        else:
            sql = None
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
        return Candidate(None, "invalid", error="Empty LLM response", flags=["PARSE_ERROR"],
                         raw=text, abstain_reason="no_sql_found")

    answer_type, sql, reason = _parse_details(text)
    declared_type = answer_type
    if not sql:
        return Candidate(
            None,
            answer_type or "invalid",
            error="No SQL found",
            flags=["PARSE_ERROR"],
            reason=reason,
            raw=text,
            abstain_reason="no_sql_found",
            declared_type=declared_type,
        )
    if answer_type == "unanswerable":
        if sql:
            return Candidate(
                sql,
                answer_type,
                error="unanswerable response must not contain SQL",
                flags=["PARSE_ERROR"],
                reason=reason,
                raw=text, declared_type=declared_type,
            )
        return Candidate(None, answer_type, reason=reason, raw=text, declared_type=declared_type)
    if answer_type not in ANSWER_TYPES:
        answer_type = "invalid"

    flags: list[str] = []
    if sql.count("(") != sql.count(")") or sql.count("'") % 2 or sql.count('"') % 2:
        flags.append("PARSE_ERROR")
    if re.search(r"(?:\bfrom|\bjoin|\bwhere|\band|\bor|,|=)\s*$", sql, re.IGNORECASE):
        flags.append("PARSE_ERROR")
    if _FORBIDDEN_SQL.search(sql) or not re.match(r"^(?:select|with)\b", sql, re.IGNORECASE):
        flags.append("UNSAFE_SQL")
    if sql.count(";"):
        flags.append("MULTI_STATEMENT")
    return Candidate(sql, answer_type, flags=flags, reason=reason, raw=text, declared_type=declared_type)


def resolve_candidate_type(candidate: Candidate, question: str) -> Candidate:
    inferred, confidence = infer_question_type(question)
    candidate.inferred_type = inferred if confidence >= 0.6 else None
    if candidate.inferred_type:
        candidate.answer_type = candidate.inferred_type
        candidate.type_source = "question"
    elif candidate.declared_type in ANSWER_TYPES:
        candidate.answer_type = candidate.declared_type
        candidate.type_source = "model"
    else:
        candidate.answer_type = "invalid" if candidate.declared_type else "undetermined"
        candidate.type_source = "none"
    return candidate


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
    global _LAST_GENERATION_OOM, _LAST_GENERATION_STATS
    import os
    kwargs = dict(
        n=count, temperature=temperature,
        max_tokens=int(os.getenv("LLM_MAX_NEW_TOKENS", "512")), seed=seed
    )
    _LAST_GENERATION_OOM = False
    try:
        outputs = backend.generate(messages, **kwargs)
    except Exception as error:
        import torch
        if not isinstance(error, torch.cuda.OutOfMemoryError):
            raise
        torch.cuda.empty_cache()
        current_tokens = getattr(backend, "last_prompt_tokens", None) or reduced_tokens_for_retry(
            messages[-1]["content"]
        )
        reduced, _, reduced_tokens = reduce_prompt(
            messages[-1]["content"], max(1, int(current_tokens * 0.6))
        )
        retry_messages = [*messages[:-1], {**messages[-1], "content": reduced}]
        try:
            outputs = backend.generate(retry_messages, **kwargs)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            _LAST_GENERATION_OOM = True
            outputs = []
    current_stats = {
        "prompt_tokens": getattr(backend, "last_prompt_tokens", reduced_tokens if "reduced_tokens" in locals() else None),
        "peak_gpu_memory": getattr(backend, "last_peak_memory", None),
    }
    previous_peak = _LAST_GENERATION_STATS.get("peak_gpu_memory")
    if previous_peak is not None and current_stats["peak_gpu_memory"] is not None:
        current_stats["peak_gpu_memory"] = max(previous_peak, current_stats["peak_gpu_memory"])
    _LAST_GENERATION_STATS = current_stats
    if not isinstance(outputs, list):
        raise TypeError("LLM backend generate() must return list[str]")
    return [output for output in outputs if isinstance(output, str)]


def reduced_tokens_for_retry(prompt: str) -> int:
    from ..tool.token_budget import prompt_token_count
    return prompt_token_count(prompt)


def generation_stats() -> dict[str, Any]:
    return dict(_LAST_GENERATION_STATS)


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
    _LAST_GENERATION_STATS.clear()
    outputs = _generate(model, messages, 1, 0.0, seed)
    if _LAST_GENERATION_OOM:
        return [Candidate(None, "invalid", error="GPU out of memory", flags=["OOM"], abstain_reason="oom")]
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


__all__ = [
    "ANSWER_TYPES", "Candidate", "generate", "generate_candidates",
    "parse_response", "resolve_candidate_type",
    "generation_stats",
]
