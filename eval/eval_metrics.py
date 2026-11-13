"""Evaluate grounded metric SQL against train gold answers."""

from __future__ import annotations

import argparse
import gc
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, "") or str(Path.cwd()) == str(Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_agent_datamate.dataprocess import load_business
from ai_agent_datamate.tool.date_resolver import resolve_time
from ai_agent_datamate.tool.metrics import (
    build_compare_sql,
    build_metric_sql,
    infer_metric_config_for_context,
)


ROOT = Path(__file__).resolve().parents[1]
ARCHETYPES = ("revenue_period", "order_count_period", "period_compare")
REVENUE_WORDS = ("doanh thu", "dthu", "bán được", "ban duoc", "giá trị bán", "gia tri ban")
ORDER_WORDS = ("số đơn", "so don", "bao nhiêu đơn", "bao nhieu don", "đơn hàng", "don hang")
COMPARE_WORDS = ("so với", "so voi", "so sánh", "so sanh", "thay đổi", "thay doi", "tăng", "tang", "giảm", "giam")
UNSUPPORTED_WORDS = (
    "trung bình", "trung binh", "bình quân", "binh quan", "cao nhất", "cao nhat",
    "nhiều nhất", "nhieu nhat", "thấp nhất", "thap nhat", "ít nhất", "it nhat",
    "tỉ lệ", "ti le", "tỷ lệ", "ty le", "bị huỷ", "bi huy", "bị hủy", "bi huy",
    "top ", "xếp hạng", "xep hang", "từ ", "tu ", "trở lên", "tro len",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _classify(
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
        if result in ARCHETYPES:
            return result
    except (ImportError, AttributeError, TypeError):
        pass
    text = question.casefold()
    if any(word in text for word in UNSUPPORTED_WORDS):
        return None
    metric = "orders" if any(word in text for word in ORDER_WORDS) else (
        "revenue" if any(word in text for word in REVENUE_WORDS) else None
    )
    if metric is None:
        return None
    if any(word in text for word in COMPARE_WORDS):
        return "period_compare"
    return "revenue_period" if metric == "revenue" else "order_count_period"


def _metric(archetype: str, question: str) -> str:
    if archetype == "revenue_period":
        return "revenue"
    if archetype == "order_count_period":
        return "orders"
    text = question.casefold()
    return "orders" if any(word in text for word in ORDER_WORDS) else "revenue"


def _periods(question: dict[str, Any], rulebook: str) -> dict[str, Any]:
    return resolve_time(question["question"], question["as_of_date"], rulebook=rulebook)["periods"]


def _value(conn: Any, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def _period_has_rows(context: Any, config: dict[str, Any], period: dict[str, Any]) -> bool:
    table = '"' + str(config["table"]).replace('"', '""') + '"'
    column = '"' + str(config["date_column"]).replace('"', '""') + '"'
    row = context.con.execute(
        f"SELECT 1 FROM {table} WHERE {column} BETWEEN "
        f"DATE '{period['start']}' AND DATE '{period['end']}' LIMIT 1",
    ).fetchone()
    return row is not None


def _equal(actual: Any, expected: Any, archetype: str) -> bool:
    if actual is None or expected is None:
        return actual is expected
    try:
        if archetype == "period_compare":
            return math.isclose(float(actual), float(expected), abs_tol=0.01)
        return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _period_text(periods: dict[str, Any]) -> str:
    return ", ".join(
        f"{name}={item['start']}..{item['end']}" for name, item in periods.items()
    ) or "không xác định"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split_dir", default="data/train")
    parser.add_argument("--business", action="append", help="Business ID; có thể truyền nhiều lần")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    split_dir = Path(args.split_dir)
    if not split_dir.is_absolute():
        split_dir = ROOT / split_dir
    questions = _read_jsonl(split_dir / "questions.jsonl")
    gold = {str(item["id"]): item for item in _read_jsonl(split_dir / "answers.jsonl")}
    selected_businesses = set(args.business or [])
    if selected_businesses:
        questions = [item for item in questions if str(item.get("business_id")) in selected_businesses]
    questions = questions[:max(args.limit, 0)]
    rulebook = (split_dir / "rulebook.md").read_text(encoding="utf-8") if (split_dir / "rulebook.md").exists() else ""
    by_business: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        by_business[str(question.get("business_id", ""))].append(question)

    report: list[str] = ["# Metric evaluation", ""]
    totals: Counter[str] = Counter()
    archetype_totals: dict[str, Counter[str]] = {name: Counter() for name in ARCHETYPES}
    wrong: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    has_entity_vocab_match_count = 0
    wrong_period_compare: list[dict[str, Any]] = []
    for business_id in sorted(by_business):
        context = None
        config: dict[str, Any] = {"ok": False, "reason": "business not loaded"}
        counts: Counter[str] = Counter()
        errors: list[dict[str, Any]] = []
        try:
            context = load_business(split_dir / "data" / business_id, business_id)
            from ai_agent_datamate.tool.qtype import build_entity_vocab
            entity_vocab = build_entity_vocab(context.con, business_id)
            config = infer_metric_config_for_context(context)
            for question in by_business[business_id]:
                qid = str(question["id"])
                question_text = str(question.get("question", ""))
                try:
                    from ai_agent_datamate.tool.qtype import (
                        is_simple_metric_question,
                        metric_entity_vocab_matches,
                        simple_metric_residual_words,
                    )
                    simple_metric, filter_reason = is_simple_metric_question(
                        question_text, conn=context.con, entity_vocab=entity_vocab,
                        business_id=business_id,
                    )
                except (ImportError, AttributeError, TypeError):
                    simple_metric, filter_reason = True, None
                    simple_metric_residual_words = lambda _: []
                    metric_entity_vocab_matches = lambda _, __: set()
                if not simple_metric:
                    item = {
                        "business": business_id,
                        "id": qid,
                        "question": question_text,
                        "reason": filter_reason,
                    }
                    if filter_reason == "has_entity":
                        item["residual_words"] = simple_metric_residual_words(question_text)
                        matches = metric_entity_vocab_matches(question_text, entity_vocab)
                        item["entity_vocab_matches"] = sorted(matches)
                        has_entity_vocab_match_count += bool(matches)
                    filtered.append(item)
                archetype = _classify(
                    question_text, conn=context.con, entity_vocab=entity_vocab,
                    business_id=business_id,
                )
                if archetype is None or qid not in gold:
                    counts["không áp dụng"] += 1
                    continue
                archetype_totals[archetype]["tested"] += 1
                try:
                    periods = _periods(question, rulebook)
                    metric = _metric(archetype, str(question["question"]))
                    if not config.get("ok"):
                        raise ValueError(config.get("reason", "invalid metric config"))
                    if archetype == "period_compare":
                        p1, p2 = periods.get("target"), periods.get("compare")
                        if not p1 or not p2:
                            raise ValueError("không giải mã được target/compare")
                        if not _period_has_rows(context, config, p1) or not _period_has_rows(context, config, p2):
                            raise ValueError("kỳ không có dòng dữ liệu")
                        sql = build_compare_sql(config, metric, p1, p2)
                    else:
                        period = next(iter(periods.values()), None)
                        if not period:
                            raise ValueError("không giải mã được kỳ")
                        if not _period_has_rows(context, config, period):
                            raise ValueError("kỳ không có dòng dữ liệu")
                        sql = build_metric_sql(config, metric, period["start"], period["end"])
                    actual = _value(context.con, sql)
                    expected = gold[qid].get("answer")
                    if _equal(actual, expected, archetype):
                        counts["đúng"] += 1
                        archetype_totals[archetype]["đúng"] += 1
                    else:
                        counts["sai"] += 1
                        archetype_totals[archetype]["sai"] += 1
                        wrong.append({
                            "business": business_id, "id": qid, "question": question["question"],
                            "periods": _period_text(periods), "sql": sql,
                            "result": actual, "gold": expected,
                        })
                        if archetype == "period_compare":
                            wrong_period_compare.append(wrong[-1])
                except Exception as error:
                    counts["không áp dụng"] += 1
                    archetype_totals[archetype]["không áp dụng"] += 1
                    errors.append({"id": qid, "reason": str(error)})
        except Exception as error:
            config = {"ok": False, "reason": str(error)}
            counts["không áp dụng"] += len(by_business[business_id])
        report.append(f"## Business `{business_id}`")
        report.append(f"- **Metric config:** `{json.dumps(config, ensure_ascii=False)}`")
        report.append(
            f"- **Kết quả:** đúng={counts['đúng']}, sai={counts['sai']}, "
            f"không áp dụng={counts['không áp dụng']}"
        )
        if errors:
            report.append(f"- **Lỗi/bỏ qua:** `{json.dumps(errors, ensure_ascii=False)}`")
        report.append("")
        totals.update(counts)
        del context
        gc.collect()

    report.extend(["## Danh sách câu sai", ""])
    if wrong:
        report.append("| Business | ID | Câu hỏi | Kỳ | SQL | Kết quả | Gold |")
        report.append("|---|---|---|---|---|---:|---:|")
        for item in wrong:
            report.append("| " + " | ".join(
                str(item[key]).replace("|", "\\|").replace("\n", " ")
                for key in ("business", "id", "question", "periods", "sql", "result", "gold")
            ) + " |")
    else:
        report.append("Không có câu sai.")
    report.extend(["", "## Câu bị loại bởi bộ lọc", ""])
    if filtered:
        for item in filtered:
            report.append(f"### `{item['id']}`")
            report.append(f"- **Business:** `{item['business']}`")
            report.append(f"- **Câu hỏi:** {item['question']}")
            report.append(f"- **Reason:** `{item['reason']}`")
            if item["reason"] == "has_entity":
                report.append(f"- **Từ còn dư:** `{', '.join(item['residual_words'])}`")
            report.append("")
        reason_counts = Counter(item["reason"] for item in filtered)
        report.append("**Đếm theo reason:**")
        for reason, count in sorted(reason_counts.items()):
            report.append(f"- `{reason}`: {count}")
    else:
        report.append("Không có câu bị loại bởi bộ lọc.")
    report.extend(["", "## Câu period_compare bị sai", ""])
    if wrong_period_compare:
        for item in wrong_period_compare:
            report.append(f"### `{item['id']}`")
            report.append(f"- **Business:** `{item['business']}`")
            report.append(f"- **Câu hỏi:** {item['question']}")
            report.append(f"- **SQL:**")
            report.append("```sql")
            report.append(str(item["sql"]))
            report.append("```")
            report.append(f"- **Kết quả:** `{item['result']}`")
            report.append(f"- **Gold:** `{item['gold']}`")
            report.append("")
    else:
        report.append("Không có câu period_compare bị sai.")
    report.extend(["", "## Tổng hợp theo archetype", "", "| Archetype | Đúng | Sai | Không áp dụng |", "|---|---:|---:|---:|"])
    for archetype in ARCHETYPES:
        counts = archetype_totals[archetype]
        report.append(
            f"| {archetype} | {counts['đúng']} | {counts['sai']} | {counts['không áp dụng']} |"
        )
    report.extend(["", f"Tổng: đúng={totals['đúng']}, sai={totals['sai']}, không áp dụng={totals['không áp dụng']}"])
    report.append(f"Câu `has_entity` có từ khớp entity vocab: {has_entity_vocab_match_count}")
    (ROOT / "outputs" / "eval_metrics.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
