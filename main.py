from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_agent_datamate.dataprocess.loader import load_business
from ai_agent_datamate.decision.checker import HEAVY_FLAGS, decide
from ai_agent_datamate.formatting.format_enforcer import to_answer
from ai_agent_datamate.llm.generator import Candidate, generate_candidates
from ai_agent_datamate.sqlrunner.validator import validate_with_retry
from ai_agent_datamate.tool.fewshot import FewShotRetriever, load_bank, retrieve_fewshots
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
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument("--time_limit_sec", type=float, default=18000)
    parser.add_argument("--debug_dir", default="outputs/debug")
    parser.add_argument("--fail_fast", action="store_true")
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
    }


def _retry_candidate(candidate: Candidate, hint: str, prompt: str, seed: int = 1234) -> str | None:
    retry_prompt = build_retry_prompt(prompt, candidate.sql or "", candidate.error or "", hint)
    retries = generate_candidates(retry_prompt, k=1, seed=seed)
    candidate._retry_count = int(getattr(candidate, "_retry_count", 0)) + 1
    return retries[0].sql if retries and retries[0].sql else None


def main() -> int:
    args = parse_args()
    if args.k != 1 or args.offset < 0 or args.time_limit_sec <= 0 or args.per_business < 1:
        raise SystemExit("--k must be 1; --offset non-negative; --time_limit_sec positive; --per_business positive")
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
    debug_path = debug_dir / "debug.jsonl"
    business_cache: dict[str, Any] = {}
    output_records: list[dict[str, Any]] = []
    errors: Counter[str] = Counter()
    error_count = 0
    started = time.monotonic()
    module_totals = {"business": 0.0, "question": 0.0, "generate": 0.0, "validate": 0.0, "decide": 0.0, "format": 0.0}

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
                "backend": backend_kind, "k": args.k,
            }
            try:
                if time.monotonic() - started >= args.time_limit_sec:
                    record = _abstain(question)
                else:
                    t = time.monotonic()
                    if business_id not in business_cache:
                        business_cache[business_id] = load_business(split_dir / "data" / business_id, business_id)
                    context = business_cache[business_id]
                    module_totals["business"] += time.monotonic() - t
                    trace["module_seconds"]["business"] = time.monotonic() - t

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
                        record = _abstain(question)
                        trace["decision"] = "abstain"
                        trace["output"] = record
                        debug.write(json.dumps(trace, ensure_ascii=False, default=str) + "\n")
                        debug.flush()
                        output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                        output.flush()
                        output_records.append(record)
                        continue
                    module_totals["question"] += time.monotonic() - t
                    trace["module_seconds"]["question"] = time.monotonic() - t
                    if index < 3:
                        (debug_dir / f"prompt_{qid}.txt").write_text(prompt, encoding="utf-8")

                    t = time.monotonic()
                    candidates = generate_candidates(prompt, 1, seed=args.seed)
                    module_totals["generate"] += time.monotonic() - t
                    trace["module_seconds"]["generate"] = time.monotonic() - t
                    t = time.monotonic()
                    for candidate in candidates:
                        validate_with_retry(
                            candidate, context,
                            retry=lambda current, hint: _retry_candidate(current, hint, prompt, args.seed),
                            max_retries=2, question=question["question"],
                        )
                    module_totals["validate"] += time.monotonic() - t
                    trace["module_seconds"]["validate"] = time.monotonic() - t
                    trace["candidates"] = [_candidate_debug(candidate) for candidate in candidates]

                    t = time.monotonic()
                    decision, winner = decide(candidates, context.capabilities, question=question["question"], periods=qcontext.periods)
                    module_totals["decide"] += time.monotonic() - t
                    trace["module_seconds"]["decide"] = time.monotonic() - t
                    if winner is not None:
                        valid = [
                            candidate for candidate in candidates
                            if candidate.canon and not (set(candidate.flags) & (HEAVY_FLAGS | ({"NO_DATE_FILTER"} if qcontext.periods else set())))
                        ]
                        trace["confidence"] = sum(candidate.canon == winner.canon for candidate in valid) / len(candidates)
                    t = time.monotonic()
                    record = to_answer(
                        qid, decision, winner.answer_type if winner else "abstain",
                        winner.columns if winner else None, winner.rows if winner else None,
                        question=question["question"],
                    )
                    module_totals["format"] += time.monotonic() - t
                    trace["module_seconds"]["format"] = time.monotonic() - t
                    trace["decision"] = decision
                    trace["output"] = record
            except Exception as error:
                record = _abstain(question)
                full_traceback = traceback.format_exc()
                short_traceback = "".join(traceback.format_exception(type(error), error, error.__traceback__, limit=5))
                trace["error"] = short_traceback
                error_count += 1
                errors[f"{type(error).__name__}: {error}"] += 1
                print(f"WARNING: question {qid} failed:\n{short_traceback}", file=sys.stderr, end="")
                if args.fail_fast:
                    trace["error"] = full_traceback
                    trace["_fail_fast_error"] = True
            trace["output"] = record
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())