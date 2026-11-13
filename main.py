from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_agent_datamate.dataprocess.loader import load_business
from ai_agent_datamate.decision.checker import blocking_flags, confidence_for_candidates, decide
from ai_agent_datamate.formatting.format_enforcer import to_answer
from ai_agent_datamate.llm.generator import (
    Candidate, generate_candidates, generation_stats, resolve_candidate_type,
)
from ai_agent_datamate.sqlrunner.validator import validate_with_retry
from ai_agent_datamate.tool.date_resolver import resolve_time
from ai_agent_datamate.tool.fewshot import FewShotRetriever, load_bank, retrieve_fewshots
from ai_agent_datamate.tool.metrics import (
    build_compare_sql,
    build_metric_sql,
    infer_metric_config_for_context,
)
from ai_agent_datamate.tool.prompts import build_prompt, build_retry_prompt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the DataMate pipeline.")
    parser.add_argument(
        "--split_dir",
        default="data/train",
        help="Split directory containing questions.jsonl and data/ (default: data/train)",
    )
    parser.add_argument("--out", default="outputs/answers.jsonl")
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of questions to process (default: 100)",
    )
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--sample_mode", choices=("first", "diverse"), default="first")
    parser.add_argument("--per_business", type=int, default=2)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--backend", choices=("mock", "hf", "vllm"), default=None)
    parser.add_argument(
        "--mode",
        choices=("hybrid", "llm_only", "resolver_only"),
        default="hybrid",
        help="Pipeline mode (default: hybrid).",
    )
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument("--time_limit_sec", type=float, default=18000)
    parser.add_argument("--debug_dir", default="outputs/debug")
    parser.add_argument(
        "--debug_out",
        default=None,
        help="Debug JSONL output path (default: <debug_dir>/debug.jsonl)",
    )
    parser.add_argument("--fail_fast", action="store_true")
    parser.add_argument(
        "--no_cache",
        action="store_true",
        help="Disable the normalized business DuckDB cache.",
    )
    parser.add_argument(
        "--resolver_only",
        action="store_true",
        help="Chỉ dùng resolver: câu nào resolver không áp dụng được hoặc bị guard thì abstain ngay, không gọi LLM và không nạp model.",
    )
    return parser.parse_args()


def _resolve_split_dir(value: str) -> Path:
    requested = Path(value)
    candidates = [requested]
    project_dir = Path(__file__).resolve().parent
    if not requested.is_absolute():
        candidates.append(project_dir / requested)
        if requested == Path("train"):
            candidates.append(project_dir / "data" / "train")
    for candidate in candidates:
        if (candidate / "questions.jsonl").is_file():
            return candidate
    expected = ", ".join(str(candidate / "questions.jsonl") for candidate in candidates)
    raise FileNotFoundError(f"Could not find questions.jsonl; checked: {expected}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _abstain(question: dict[str, Any]) -> dict[str, str]:
    return {"id": str(question["id"]), "type": "abstain"}


def _candidate_debug(candidate: Candidate) -> dict[str, Any]:
    return {
        "type": candidate.answer_type,
        "sql": candidate.sql,
        "error": candidate.error,
        "flags": list(candidate.flags),
        "raw_response": candidate.raw,
        "retry_count": int(getattr(candidate, "_retry_count", 0)),
        "result_head": (candidate.rows or [])[:5],
        "abstain_reason": getattr(candidate, "abstain_reason", None),
        "type_source": candidate.type_source,
        "inferred_type": candidate.inferred_type,
        "declared_type": candidate.declared_type,
    }


def _classify_metric_question(
    question: str,
    *,
    conn: Any = None,
    entity_vocab: set[str] | None = None,
    business_id: str | None = None,
) -> str | None:
    try:
        from ai_agent_datamate.tool.qtype import classify_question, is_simple_metric_question
        if not is_simple_metric_question(
            question, conn=conn, entity_vocab=entity_vocab, business_id=business_id
        )[0]:
            return None
        result = classify_question(question)
        result = result.get("archetype") if isinstance(result, dict) else result
        if result in {"revenue_period", "order_count_period", "period_compare",
                      "order_count_cancelled", "cancel_rate"}:
            return result
    except (ImportError, AttributeError, TypeError):
        pass
    text = question.casefold()
    unsupported = (
        "trung bình", "trung binh", "bình quân", "binh quan", "cao nhất", "cao nhat",
        "nhiều nhất", "nhieu nhat", "tỉ lệ", "ti le", "tỷ lệ", "ty le",
        "bị huỷ", "bi huy", "bị hủy", "top ", "xếp hạng", "xep hang",
    )
    if any(token in text for token in unsupported):
        return None
    order_words = ("số đơn", "so don", "bao nhiêu đơn", "bao nhieu don", "đơn hàng", "don hang")
    revenue_words = ("doanh thu", "dthu", "bán được", "ban duoc", "giá trị bán", "gia tri ban")
    metric = "orders" if any(token in text for token in order_words) else (
        "revenue" if any(token in text for token in revenue_words) else None
    )
    if metric is None:
        return None
    compare_words = ("so với", "so voi", "so sánh", "so sanh", "thay đổi", "thay doi", "tăng", "tang", "giảm", "giam")
    if any(token in text for token in compare_words):
        return "period_compare"
    return "order_count_period" if metric == "orders" else "revenue_period"


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _period_bounds(period: Any) -> tuple[str, str]:
    if isinstance(period, dict):
        start, end = period.get("start", ""), period.get("end", "")
    else:
        start, end = period[0], period[1]
    return str(start), str(end)


def _period_overlaps_data(config: dict[str, Any], period: Any) -> bool:
    min_date, max_date = config.get("min_date"), config.get("max_date")
    if not min_date or not max_date:
        return True
    start, end = _period_bounds(period)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        return True
    return not (end < min_date or start > max_date)


def _period_coverage_reason(config: dict[str, Any], period: Any) -> str | None:
    min_date, max_date = config.get("min_date"), config.get("max_date")
    if not min_date or not max_date:
        return None
    start, end = _period_bounds(period)
    if end < min_date or start > max_date:
        return f"Kỳ yêu cầu ({start}..{end}) nằm ngoài khoảng ngày có dữ liệu ({min_date}..{max_date})"
    return None


def _unanswerable_candidate(reason: str) -> Candidate:
    return Candidate(
        sql=None, answer_type="unanswerable", raw="resolver",
        reason=str(reason), abstain_reason="period_outside_data",
        type_source="resolver", inferred_type="unanswerable",
    )


def _period_has_rows(context: Any, config: dict[str, Any], period: Any) -> bool:
    start, end = _period_bounds(period)
    table = _quoted(config["table"])
    date_column = _quoted(config["date_column"])
    try:
        row = context.con.execute(
            f"SELECT 1 FROM {table} WHERE {date_column} BETWEEN DATE '{start}' AND DATE '{end}' LIMIT 1"
        ).fetchone()
        return row is not None
    except Exception:
        return True  # coverage-specific guard questions should not crash the resolver path here


_RESOLVER_UNSUPPORTED = (
    "huy", "tra hang", "tra lai",
    "ngay nao", "thang nao", "hom nao",
    "nhieu nhat", "it nhat", "cao nhat", "dat nhat",
    "binh quan", "trung binh", "moi don",
    "top", "xep hang",
    "dong gop", "chiem", "ti le", "ty le",
    "san pham", "mat hang", "hang nao",
    "khach hang", "ma khach", "ten khach", "khach nao", "khach chi tieu",
)


def _unsupported_resolver_shape(question: str) -> bool:
    text = unicodedata.normalize("NFD", question.casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn").replace("đ", "d")
    return any(token in text for token in _RESOLVER_UNSUPPORTED)


def _resolver_candidate(
    context: Any,
    config: dict[str, Any],
    archetype: str | None,
    question: str,
    periods: dict[str, Any],
    entity_vocab: set[str] | None = None,
) -> tuple[Candidate | None, str | None]:
    try:
        from ai_agent_datamate.tool.qtype import is_simple_metric_question
        allowed, reason = is_simple_metric_question(
            question, conn=context.con, entity_vocab=entity_vocab
        )
        if not allowed:
            return None, reason
    except (ImportError, AttributeError, TypeError):
        return None, "metric_question_check_unavailable"
    if archetype not in {"revenue_period", "order_count_period", "period_compare",
                         "order_count_cancelled", "cancel_rate"}:
        return None, "unsupported_shape"
    if not config.get("ok"):
        return None, f"infer_config_failed:{config.get('reason', 'unknown')}"
    if not getattr(context, "coverage_safe", True):
        return None, "coverage_parse_warning"
    metric = (
        "cancelled_orders" if archetype == "order_count_cancelled"
        else "cancel_rate" if archetype == "cancel_rate"
        else "orders" if archetype == "order_count_period" else "revenue"
    )
    try:
        if archetype == "period_compare":
            first, second = periods.get("target"), periods.get("compare")
            if not first or not second:
                return None, "no_period"
            for label, period in (("target", first), ("compare", second)):
                outside_reason = _period_coverage_reason(config, period)
                if outside_reason:
                    return _unanswerable_candidate(outside_reason), "period_outside_data"
                if not _period_overlaps_data(config, period):
                    return None, "gap_inside_range"
                if not _period_has_rows(context, config, period):
                    return None, "gap_inside_range"
            sql = build_compare_sql(config, metric, first, second)
            answer_type = "percent"
        else:
            candidates = [period for name, period in periods.items() if name not in {"compare", "same_period_last_year"}]
            if len(candidates) != 1:
                return None, "no_period"
            period = candidates[0]
            outside_reason = _period_coverage_reason(config, period)
            if outside_reason:
                return _unanswerable_candidate(outside_reason), "period_outside_data"
            if not _period_overlaps_data(config, period):
                return None, "gap_inside_range"
            if not _period_has_rows(context, config, period):
                return None, "gap_inside_range"
            sql = build_metric_sql(config, metric, period["start"], period["end"])
            answer_type = "percent" if archetype == "cancel_rate" else "number"
        result = context.con.execute(sql)
        rows = result.fetchall()
        if not rows or rows[0][0] is None:
            return None, "empty_result"
        if archetype == "order_count_period" and rows[0][0] == 0:
            return None, "orders_zero"
        columns = [str(item[0]) for item in result.description]
        candidate = Candidate(
            sql=sql,
            answer_type=answer_type,
            rows=[list(row) for row in rows],
            columns=columns,
            flags=["RESOLVER"],
            raw="resolver",
            type_source="resolver",
            inferred_type=answer_type,
        )
        return candidate, None
    except Exception as error:
        return None, f"resolver_error:{type(error).__name__}"


def _retry_candidate(candidate: Candidate, hint: str, prompt: str, seed: int = 1234) -> str | None:
    retry_prompt = build_retry_prompt(prompt, candidate.sql or "", candidate.error or "", hint)
    retries = generate_candidates(retry_prompt, k=1, seed=seed)
    candidate._retry_count = int(getattr(candidate, "_retry_count", 0)) + 1
    return retries[0].sql if retries and retries[0].sql else None


def main() -> int:
    args = parse_args()
    mode = "resolver_only" if args.resolver_only else args.mode
    if args.k < 1 or args.offset < 0 or args.time_limit_sec <= 0 or args.per_business < 1:
        raise SystemExit("--k positive; --offset non-negative; --time_limit_sec positive; --per_business positive")
    backend_kind = args.backend or os.environ.get("LLM_BACKEND", "hf")
    os.environ["LLM_BACKEND"] = backend_kind
    backend_model = os.environ.get("HF_MODEL", "Qwen/Qwen3-14B") if backend_kind == "hf" else os.environ.get("LLM_MODEL_PATH", backend_kind)
    print(f"[backend] {backend_kind} model={backend_model}", flush=True)

    split_dir = _resolve_split_dir(args.split_dir)
    questions = sorted(
        _read_jsonl(split_dir / "questions.jsonl"),
        key=lambda item: (str(item.get("business_id", "")), str(item.get("id", ""))),
    )
    if args.sample_mode == "diverse":
        selected: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for item in questions:
            business_id = str(item.get("business_id", ""))
            if counts.get(business_id, 0) >= args.per_business:
                continue
            selected.append(item)
            counts[business_id] = counts.get(business_id, 0) + 1
        questions = selected
        print("Selected businesses:")
        for business_id in sorted(counts):
            suffixes = sorted({
                path.suffix.lower().lstrip(".")
                for path in (split_dir / "data" / business_id).glob("*")
                if path.is_file() and path.suffix.lower() in {".csv", ".xlsx", ".xls", ".sqlite", ".db"}
            })
            print(f"  {business_id}: {', '.join(suffixes) or '(none)'}")
    questions = questions[args.offset : args.offset + args.limit if args.limit is not None else None]
    rulebook_path = split_dir / "rulebook.md"
    rulebook = rulebook_path.read_text(encoding="utf-8") if rulebook_path.exists() else ""
    calendar_path = split_dir / "calendar.csv"
    calendar = pd.read_csv(calendar_path) if calendar_path.exists() else None
    bank_path = split_dir / "fewshot_bank.jsonl"
    retriever = FewShotRetriever(load_bank(bank_path)) if bank_path.exists() else None

    out_path = Path(args.out)
    debug_dir = Path(args.debug_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_path = Path(args.debug_out) if args.debug_out else debug_dir / "debug.jsonl"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    business_cache: dict[str, Any] = {}
    entity_vocab_cache: dict[str, set[str]] = {}
    metric_cache: dict[str, dict[str, Any]] = {}
    output_records: list[dict[str, Any]] = []
    errors: Counter[str] = Counter()
    error_count = 0
    resolver_count = 0
    llm_count = 0
    has_entity_vocab_match_count = 0
    skip_reasons: Counter[str] = Counter()
    started = time.monotonic()
    module_totals = {"business": 0.0, "build_vocab": 0.0, "periods": 0.0, "resolver": 0.0, "generate": 0.0, "validate": 0.0, "decide": 0.0, "format": 0.0}

    with out_path.open("w", encoding="utf-8") as output, debug_path.open("w", encoding="utf-8") as debug:
        for index, question in enumerate(questions):
            qid = str(question.get("id", ""))
            business_id = str(question.get("business_id", ""))
            q_started = time.monotonic()
            trace: dict[str, Any] = {
                "id": qid, "business_id": business_id, "question": question.get("question", ""),
                "as_of_date": question.get("as_of_date"), "periods": {}, "entity_hints": [],
                "candidates": [], "decision": "abstain", "confidence": 0.0, "output": None,
                "module_seconds": {},
                "cache_hit": False,
                "backend": backend_kind, "k": args.k,
                "path": "llm",
                "resolver_skip_reason": "mode_llm_only" if mode == "llm_only" else None,
                "prompt_tokens": None,
                "peak_gpu_memory": None,
            }
            try:
                if time.monotonic() - started >= args.time_limit_sec:
                    record = _abstain(question)
                else:
                    t = time.monotonic()
                    if business_id not in business_cache:
                        business_cache[business_id] = load_business(
                            split_dir / "data" / business_id,
                            business_id,
                            use_cache=not args.no_cache,
                        )
                    context = business_cache[business_id]
                    load_seconds = time.monotonic() - t
                    module_totals["business"] += load_seconds
                    trace["module_seconds"]["business"] = load_seconds
                    trace["cache_hit"] = context.cache_hit
                    entity_vocab = None
                    if mode == "llm_only":
                        simple_metric, resolver_reason = False, "mode_llm_only"
                    else:
                        from ai_agent_datamate.tool.qtype import (
                            build_entity_vocab,
                            is_simple_metric_question,
                            metric_entity_vocab_matches,
                            simple_metric_residual_words,
                        )
                        residual = simple_metric_residual_words(question["question"])
                        if residual:
                            t = time.monotonic()
                            if business_id not in entity_vocab_cache:
                                entity_vocab_cache[business_id] = build_entity_vocab(context.con, business_id)
                            entity_vocab = entity_vocab_cache[business_id]
                            vocab_seconds = time.monotonic() - t
                            module_totals["build_vocab"] += vocab_seconds
                            trace["module_seconds"]["build_vocab"] = vocab_seconds
                        simple_metric, resolver_reason = is_simple_metric_question(
                            question["question"], conn=context.con,
                            entity_vocab=entity_vocab, business_id=business_id,
                        )
                    trace["has_entity_vocab_matches"] = sorted(
                        metric_entity_vocab_matches(question["question"], entity_vocab)
                        if mode != "llm_only" else []
                    )
                    has_entity_vocab_match_count += bool(
                        trace["has_entity_vocab_matches"] and resolver_reason == "has_entity"
                    )
                    archetype = (
                        _classify_metric_question(
                            question["question"], conn=context.con,
                            entity_vocab=entity_vocab, business_id=business_id,
                        )
                        if simple_metric else None
                    )
                    if mode != "llm_only" and business_id not in metric_cache:
                        metric_cache[business_id] = infer_metric_config_for_context(context)
                    t = time.monotonic()
                    periods = resolve_time(
                        question["question"], question["as_of_date"],
                        calendar=calendar, rulebook=rulebook,
                    )["periods"]
                    trace["periods"] = periods
                    periods_seconds = time.monotonic() - t
                    module_totals["periods"] += periods_seconds
                    trace["module_seconds"]["periods"] = periods_seconds
                    t = time.monotonic()
                    if mode == "llm_only":
                        resolver_cand, candidate_reason = None, "mode_llm_only"
                    else:
                        resolver_cand, candidate_reason = _resolver_candidate(
                            context, metric_cache[business_id], archetype,
                            question["question"], periods,
                            entity_vocab,
                        )
                    resolver_seconds = time.monotonic() - t
                    module_totals["resolver"] += resolver_seconds
                    trace["module_seconds"]["resolver"] = resolver_seconds
                    resolver_reason = candidate_reason or resolver_reason
                    if resolver_cand is not None:
                        if resolver_cand.answer_type == "unanswerable":
                            reason = str(resolver_cand.reason or "period_outside_data")
                            record = to_answer(
                                qid, "unanswerable", "unanswerable",
                                question=question["question"], reason=reason,
                            )
                            trace["path"] = "resolver_unanswerable"
                            trace["resolver_skip_reason"] = "period_outside_data"
                            trace["decision"] = "unanswerable"
                            trace["abstain_reason"] = None
                            trace["output"] = record
                            trace["module_seconds"]["elapsed"] = time.monotonic() - q_started
                            debug.write(json.dumps(trace, ensure_ascii=False, default=str) + "\n")
                            debug.flush()
                            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                            output.flush()
                            output_records.append(record)
                            continue
                        candidates = [resolver_cand]
                        trace["path"] = "resolver"
                        resolver_count += 1
                    else:
                        trace["resolver_skip_reason"] = resolver_reason
                        skip_reasons[resolver_reason or "none"] += 1
                        if mode == "resolver_only":
                            record = _abstain(question)
                            trace["path"] = "abstain"
                            trace["decision"] = "abstain"
                            trace["abstain_reason"] = f"resolver_only:{resolver_reason}"
                            trace["output"] = record
                            trace["module_seconds"]["elapsed"] = time.monotonic() - q_started
                            debug.write(json.dumps(trace, ensure_ascii=False, default=str) + "\n")
                            debug.flush()
                            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                            output.flush()
                            output_records.append(record)
                            continue
                        t = time.monotonic()
                        fewshots = retrieve_fewshots(question["question"], retriever, exclude_business_id=business_id) if retriever else []
                        qcontext, prompt = build_prompt(
                            question["question"], question["as_of_date"], context,
                            rulebook=rulebook, fewshots=fewshots, calendar=calendar,
                        )
                        trace["periods"] = qcontext.periods
                        trace["entity_hints"] = [hint.__dict__ for hint in qcontext.entity_hints]
                        trace["prompt_tokens_estimate"] = qcontext.prompt_tokens_estimate
                        trace["prompt_reduction_steps"] = qcontext.prompt_reduction_steps
                        trace["prompt_part_tokens"] = qcontext.prompt_part_tokens
                        if qcontext.prompt_over_budget:
                            trace["abstain_reason"] = "prompt_over_budget_after_all_reductions"
                            trace["path"] = "abstain"
                            if mode != "llm_only":
                                trace["resolver_skip_reason"] = "prompt_over_budget"
                            record = _abstain(question)
                            trace["decision"] = "abstain"
                            trace["output"] = record
                            trace["module_seconds"]["elapsed"] = time.monotonic() - q_started
                            debug.write(json.dumps(trace, ensure_ascii=False, default=str) + "\n")
                            debug.flush()
                            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                            output.flush()
                            output_records.append(record)
                            continue
                        if index < 3:
                            (debug_dir / f"prompt_{qid}.txt").write_text(prompt, encoding="utf-8")
                        llm_count += 1
                        candidates = generate_candidates(prompt, args.k, seed=args.seed)
                        trace.update(generation_stats())
                        for candidate in candidates:
                            resolve_candidate_type(candidate, question["question"])
                        generate_seconds = time.monotonic() - t
                        module_totals["generate"] += generate_seconds
                        trace["module_seconds"]["generate"] = generate_seconds
                    t = time.monotonic()
                    if trace["path"] == "llm":
                        for candidate in candidates:
                            validate_with_retry(
                                candidate, context,
                                retry=lambda current, hint: _retry_candidate(current, hint, prompt, args.seed),
                                max_retries=2, question=question["question"],
                            )
                    module_totals["validate"] += time.monotonic() - t
                    trace["module_seconds"]["validate"] = time.monotonic() - t
                    t = time.monotonic()
                    decision, winner = decide(candidates, context.capabilities, question=question["question"], periods=periods)
                    module_totals["decide"] += time.monotonic() - t
                    trace["module_seconds"]["decide"] = time.monotonic() - t
                    trace["confidence"] = confidence_for_candidates(candidates, winner)
                    if winner is None:
                        reasons = [getattr(candidate, "abstain_reason", None) for candidate in candidates]
                        trace["abstain_reason"] = next((reason for reason in reasons if reason), "no_eligible_candidate")
                    else:
                        trace["abstain_reason"] = None
                    trace["candidates"] = [_candidate_debug(candidate) for candidate in candidates]
                    trace["blocking_flags"] = sorted({
                        flag
                        for candidate in candidates
                        for flag in blocking_flags(candidate, periods)
                        if (getattr(candidate, "abstain_reason", None) or "").startswith("hard_blocking_flags:")
                    })
                    t = time.monotonic()
                    record = to_answer(
                        qid, decision, winner.answer_type if winner else "abstain",
                        winner.columns if winner else None, winner.rows if winner else None,
                        question=question["question"],
                        reason=next(
                            (candidate.reason for candidate in candidates
                             if decision == "unanswerable" and candidate.reason),
                            None,
                        ),
                    )
                    module_totals["format"] += time.monotonic() - t
                    trace["module_seconds"]["format"] = time.monotonic() - t
                    trace["decision"] = (
                        "answer" if decision == "answer"
                        else "unanswerable" if decision == "unanswerable"
                        else "abstain"
                    )
                    if trace["decision"] == "abstain":
                        trace["path"] = "abstain"
                    trace["output"] = record
            except Exception as error:
                record = _abstain(question)
                full_traceback = traceback.format_exc()
                short_traceback = "".join(traceback.format_exception(type(error), error, error.__traceback__, limit=5))
                trace["error"] = short_traceback
                error_count += 1
                trace["path"] = "abstain"
                if mode == "resolver_only" and not trace.get("resolver_skip_reason"):
                    trace["resolver_skip_reason"] = f"error:{type(error).__name__}"
                errors[f"{type(error).__name__}: {error}"] += 1
                print(f"WARNING: question {qid} failed:\n{short_traceback}", file=sys.stderr, end="")
                if args.fail_fast:
                    trace["error"] = full_traceback
                    trace["_fail_fast_error"] = True
            trace["output"] = record
            if mode == "resolver_only" and trace["decision"] != "answer" and not trace.get("resolver_skip_reason"):
                trace["resolver_skip_reason"] = (
                    f"resolver_not_attempted:{trace.get('abstain_reason') or 'unknown'}"
                )
            trace["module_seconds"]["elapsed"] = time.monotonic() - q_started
            debug.write(json.dumps(trace, ensure_ascii=False, default=str) + "\n")
            debug.flush()
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            output.flush()
            output_records.append(record)
            if trace.get("_fail_fast_error"):
                print(trace["error"], file=sys.stderr, end="")
                raise RuntimeError(f"fail_fast: question {qid} failed")

    expected_ids = [str(item.get("id", "")) for item in questions]
    actual_ids = [str(item.get("id", "")) for item in output_records]
    if len(output_records) != len(questions) or actual_ids != expected_ids:
        raise RuntimeError("Output count or ID sequence does not match processed questions")
    processed = len(output_records) or 1
    print(f"Processed {len(output_records)} questions; output={out_path}")
    print(f"Errors: {error_count}/{len(output_records)}")
    if errors:
        print("Most common errors:")
        for message, count in errors.most_common(3):
            print(f"  {count}x {message}")
    print("Average module seconds: " + ", ".join(f"{name}={value / processed:.4f}" for name, value in module_totals.items()))
    print(f"Resolver answers: {resolver_count}")
    print(f"has_entity with entity vocab matches: {has_entity_vocab_match_count}")
    print(f"LLM answers: {llm_count}")
    print("Resolver skip reasons: " + (", ".join(f"{reason}={count}" for reason, count in skip_reasons.most_common()) or "(none)"))
    debug_records = [
        json.loads(line)
        for line in debug_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decisions = Counter(trace.get("decision") for trace in debug_records)
    abstain_reasons = Counter(
        trace.get("abstain_reason") or "unknown"
        for trace in debug_records
        if trace.get("decision") == "abstain"
    )
    debug_skip_reasons = Counter(
        trace.get("resolver_skip_reason")
        for trace in debug_records
        if trace.get("resolver_skip_reason")
    )
    print(f"Total questions: {len(output_records)}")
    print(f"Answers: {decisions.get('answer', 0)}")
    print(f"Abstains: {decisions.get('abstain', 0)}")
    if mode == "llm_only":
        print("LLM abstain reasons: " + (
            ", ".join(f"{reason}={count}" for reason, count in abstain_reasons.most_common())
            or "(none)"
        ))
        average_question_seconds = sum(
            float(trace.get("module_seconds", {}).get("elapsed", 0.0))
            for trace in debug_records
        ) / processed
        print(f"Average question seconds: {average_question_seconds:.4f}")
    print("Abstain resolver skip reasons: " + (
        ", ".join(
            f"{reason}={count}"
            for reason, count in debug_skip_reasons.most_common()
        ) or "(none)"
    ))
    print(f"Total runtime seconds: {time.monotonic() - started:.3f}")
    print("5 slowest questions:")
    for slow in sorted(
        debug_records,
        key=lambda item: float(item.get("module_seconds", {}).get("elapsed", 0.0)),
        reverse=True,
    )[:5]:
        steps = slow.get("module_seconds", {})
        step_name, step_seconds = max(
            ((name, value) for name, value in steps.items() if name != "elapsed"),
            key=lambda item: float(item[1]),
            default=("none", 0.0),
        )
        print(
            f"  {slow.get('id')}: elapsed={float(steps.get('elapsed', 0.0)):.3f}s "
            f"slowest={step_name}({float(step_seconds):.3f}s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())