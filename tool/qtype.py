"""Question classification rules for Vietnamese DataMate questions."""

from __future__ import annotations

import re
import unicodedata
import json
from pathlib import Path
from typing import Any


_ENTITY_VOCAB_CACHE: dict[str, set[str]] = {}


def _normalize(question: str) -> str:
    text = unicodedata.normalize("NFD", str(question).lower().replace("đ", "d"))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return " ".join(text.split())


def _simple_normalize(question: str) -> str:
    text = _normalize(question)
    text = "".join(
        " " if unicodedata.category(char)[0] in {"P", "S"} else char
        for char in text
    )
    return " ".join(text.split())


_GUARD_PATTERNS = (
    ("unsupported_shape", r"\b(?:huy|tra hang|hoan hang)\b"),
    ("unsupported_shape", r"\b(?:ngay nao|thang nao|hom nao|nhieu nhat|it nhat|cao nhat)\b"),
    ("unsupported_shape", r"\b(?:binh quan|trung binh|moi don|top|xep hang|chiem|ti le|ty le)\b"),
)
_PERIOD_PATTERNS = (
    r"\bthang\s+\d{1,2}(?:\s+(?:nam\s+)?\d{4})?\b",
    r"\b(?:quy|quys)\s+\d{1,2}(?:\s+(?:nam\s+)?\d{4})?\b",
    r"\bnam\s+\d{4}\b",
    r"\b(?:hom nay|hnay|hom qua|tuan truoc|thang truoc)\b",
)
_COMMON_WORDS = (
    "cua hang", "cua", "la", "cho", "toi", "minh", "biet", "hoi", "giup",
    "xem", "gium", "trong", "duoc", "ban", "nhi", "nhe", "a", "z", "vay",
    "the nao", "nhu the nao", "tien", "thang lien truoc", "lien truoc", "ta",
)
_COMMON_TOKENS = {
    token
    for phrase in _COMMON_WORDS
    for token in phrase.split()
    if len(token) >= 3
}
_QUANTITY_RE = re.compile(
    r"^\d+(?:[.,]\d+)?(?:g|kg|mg|ml|l|lit|cai|chai|hop|vien|bo|goi|thung)$"
)
_QUANTITY_PAIR_RE = re.compile(
    r"^\d+(?:[.,]\d+)?\s+(?:cai|chai|hop|vien|bo|goi|thung|kg|g|ml|l)$"
)
_EXCLUDED_ENTITY_COLUMN_RE = re.compile(
    r"(?:^|_)(?:ngay|date|time|thoi_gian|status|trang_thai|ma_don|so_don|"
    r"don_hang|order|invoice|hoa_don)(?:$|_)"
)


def build_entity_vocab(conn: Any, business_id: str | None = None) -> set[str]:
    """Build normalized entity tokens from loaded VARCHAR columns."""
    cache_key = business_id or str(getattr(conn, "business_id", id(conn)))
    if cache_key in _ENTITY_VOCAB_CACHE:
        return _ENTITY_VOCAB_CACHE[cache_key]
    cache_path = Path("outputs/cache") / f"vocab_{re.sub(r'[^A-Za-z0-9_.-]', '_', cache_key)}.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(cached, list):
            vocab = {str(item) for item in cached}
            _ENTITY_VOCAB_CACHE[cache_key] = vocab
            return vocab
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    vocab: set[str] = set()
    try:
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type IN ('BASE TABLE', 'VIEW')"
        ).fetchall()
        for (table,) in tables:
            table_name = str(table)
            for column, dtype, *_ in conn.execute(
                f'DESCRIBE "{table_name.replace(chr(34), chr(34) * 2)}"'
            ).fetchall():
                column_name = str(column)
                if "VARCHAR" not in str(dtype).upper() or _EXCLUDED_ENTITY_COLUMN_RE.search(
                    _simple_normalize(column_name).replace(" ", "_")
                ):
                    continue
                quoted_table = '"' + table_name.replace('"', '""') + '"'
                quoted_column = '"' + column_name.replace('"', '""') + '"'
                count = conn.execute(
                    f"SELECT COUNT(DISTINCT {quoted_column}) FROM {quoted_table}"
                ).fetchone()[0]
                if count is None or int(count) > 50000:
                    continue
                values = conn.execute(
                    f"SELECT DISTINCT {quoted_column} FROM {quoted_table}"
                ).fetchall()
                for (value,) in values:
                    if value is None:
                        continue
                    for token in re.findall(r"[a-z0-9]+", _simple_normalize(str(value))):
                        if len(token) >= 3 and token not in _COMMON_TOKENS:
                            vocab.add(token)
    except Exception:
        vocab = set()
    _ENTITY_VOCAB_CACHE[cache_key] = vocab
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(sorted(vocab), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return vocab


def is_simple_metric_question(
    question: str,
    *,
    conn: Any = None,
    entity_vocab: set[str] | None = None,
    business_id: str | None = None,
) -> tuple[bool, str]:
    """Decide whether a question is safe for the aggregate metric resolver."""
    text = _simple_normalize(question)
    is_cancel_count = bool(re.search(r"\b(?:dem|so don).*\b(?:huy|huỷ)\b|\b(?:huy|huỷ).*\b(?:dem|so don)\b", text))
    is_cancel_rate = bool(re.search(r"\b(?:ti le|ty le).*\b(?:huy|huỷ)\b|\b(?:huy|huỷ).*\b(?:ti le|ty le)\b", text))
    if is_cancel_count:
        return True, "cancelled_orders"
    if is_cancel_rate:
        return True, "cancel_rate"
    for reason, pattern in _GUARD_PATTERNS:
        if re.search(pattern, text):
            return False, reason

    quantity = re.search(
        r"\b(?:so|bao nhieu)\s+(?:bo|cai|chai|hop|vien|luong|kg)\b", text
    )
    if quantity:
        return False, "quantity_question"

    residual = [token for token in _remove_known_metric_phrases(text).split() if token]
    if "ca" in residual and ("sang" in residual or "chieu" in residual):
        return False, "has_entity"
    if residual and entity_vocab is None and conn is not None:
        entity_vocab = build_entity_vocab(conn, business_id)
    if residual and (
        (entity_vocab and any(token in entity_vocab for token in residual))
        or _has_quantity_or_packaging(residual)
        or len(residual) >= 3
    ):
        return False, "has_entity"

    has_revenue = bool(
        re.search(r"\b(?:doanh thu|dthu|revenue|tong cong|bao nhieu tien)\b", text)
        or re.search(r"\bban duoc\b.*\btien\b", text)
    )
    has_orders = bool(
        re.search(r"\b(?:so don|may don|bao nhieu don|don hang)\b", text)
    )
    if has_orders:
        return True, "orders"
    if has_revenue:
        return True, "revenue"
    return False, "not_metric_question"


def _remove_known_metric_phrases(text: str) -> str:
    remaining = text
    remaining = re.sub(r"\bngay\s+(?=hom nay|hom qua)\b", " ", remaining)
    remaining = re.sub(r"\b(?:tinh rieng|dc|duoc|don hang)\b", " ", remaining)
    for pattern in _PERIOD_PATTERNS:
        remaining = re.sub(pattern, " ", remaining)
    remaining = re.sub(
        r"\b(?:so voi|tang hay giam|thay doi|bao nhieu phan tram|phan tram)\b|%",
        " ",
        remaining,
    )
    remaining = re.sub(
        r"\b(?:doanh thu|dthu|doanh so|revenue|so don|don hang|tong cong|tong|"
        r"bao nhieu tien|bao nhieu don|bao nhieu|bao nhiu|bnhiu|bn|may don|may|so)\b",
        " ",
        remaining,
    )
    for phrase in sorted(_COMMON_WORDS, key=len, reverse=True):
        remaining = re.sub(rf"\b{re.escape(phrase)}\b", " ", remaining)
    return remaining


def _has_quantity_or_packaging(tokens: list[str]) -> bool:
    return any(
        _QUANTITY_RE.fullmatch(token)
        or _QUANTITY_PAIR_RE.fullmatch(f"{token} {next_token}")
        for token, next_token in zip(tokens, tokens[1:])
    ) or any(_QUANTITY_RE.fullmatch(token) for token in tokens)


def simple_metric_residual_words(question: str) -> list[str]:
    """Return meaningful words left after removing known metric phrases."""
    return [token for token in _remove_known_metric_phrases(_simple_normalize(question)).split()
            if len(token) >= 2]


def metric_entity_vocab_matches(question: str, entity_vocab: set[str]) -> set[str]:
    """Return residual words recognized by the loaded business vocabulary."""
    return {
        token for token in simple_metric_residual_words(question)
        if token in entity_vocab
    }


def metric_guard_reason(question: str) -> str | None:
    """Return a reason when the metric resolver must abstain for this shape."""
    allowed, reason = is_simple_metric_question(question)
    return None if allowed or reason == "not_metric_question" else reason


def classify_question(question: str) -> dict[str, Any]:
    """Classify answer shape and the supported metric archetype."""
    text = _normalize(question)
    has_percent = bool(
        re.search(r"%(?:\s|$)|\b(?:ti le|ty le|tir le|phan tram|chiem)\b", text)
        or re.search(r"\b(?:tang|giam)\s+(?:bao nhieu\s*)?%?", text)
    )
    has_date = bool(re.search(r"\b(?:ngay nao|thang nao|hom nao)\b", text))
    has_list = bool(
        re.search(r"\btop\s*\d*\b", text)
        or re.search(r"\b(?:lan luot|liet ke|ghi hai so|danh sach)\b", text)
    )
    has_boolean = bool(re.search(r"\b(?:co phai|dung khong|phai khong)\b", text))
    has_order_count = bool(
        re.search(r"\b(?:bao nhieu|bnhiu|bn|may)\s+don(?: hang)?\b", text)
        or re.search(r"\b(?:so don|don hang)\b", text)
        or (re.search(r"\bdon\b", text) and not re.search(r"\bdon gia\b", text))
    )
    has_revenue = bool(
        re.search(r"\b(?:doanh thu|dthu|ban duoc|gia tri ban|tong tien)\b", text)
        or "ban duoc" in text
    )
    is_cancelled_or_returned = bool(
        re.search(r"\b(?:huy|tra hang|hoan hang|don bi huy|don da huy)\b", text)
    )
    has_compare = bool(
        re.search(r"\b(?:so voi|so sanh|thay doi|tang|giam)\b", text)
    )

    if has_percent:
        answer_type = "percent"
    elif has_date:
        answer_type = "date"
    elif has_list:
        answer_type = "list"
    elif has_boolean:
        answer_type = "boolean"
    elif has_order_count or re.search(r"\b(?:ton kho|don gia)\b", text):
        answer_type = "number"
    elif re.search(r"\b(?:ai|khach nao|san pham nao|mat hang nao)\b", text):
        answer_type = "string"
    else:
        answer_type = "number" if (
            has_revenue
            or has_order_count
            or re.search(r"\b(?:bao nhieu|may|tong)\b", text)
        ) else "string"

    metric = "orders" if has_order_count else "revenue" if has_revenue else None
    if metric == "orders" and is_cancelled_or_returned and re.search(r"\b(?:huy|huỷ)\b", text):
        archetype = "cancel_rate" if has_percent else "order_count_cancelled"
    elif metric == "orders" and re.search(r"\b(?:ti le|ty le)\b", text):
        archetype = "cancel_rate"
    elif metric and has_compare:
        archetype = "period_compare"
    elif metric == "revenue" and not has_date and not has_list:
        archetype = "revenue_period"
    elif metric == "orders" and not is_cancelled_or_returned and not has_date and not has_list:
        archetype = "order_count_period"
    else:
        archetype = "other"

    return {"type": answer_type, "archetype": archetype, "metric": metric}


__all__ = [
    "classify_question",
    "build_entity_vocab",
    "is_simple_metric_question",
    "metric_guard_reason",
    "metric_entity_vocab_matches",
    "simple_metric_residual_words",
]
