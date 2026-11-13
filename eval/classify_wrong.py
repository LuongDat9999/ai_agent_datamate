import re, pathlib

MISSING = {"2025-04", "2025-05", "2025-11", "2026-02", "2026-04"}
UNSUP = re.compile(r"hu[ỷy]|hủy|trả hàng|ngày nào|tháng nào|nhiều nhất|ít nhất|bình quân|trung bình|mỗi đơn|top|chiếm|tỉ lệ", re.I)

def num(s):
    try:
        return float(s.strip().replace(",", ""))
    except Exception:
        return None

rows = []
for line in pathlib.Path("outputs/eval_metrics.md").read_text(encoding="utf8").splitlines():
    if not line.startswith("| biz_"):
        continue
    c = [x.strip() for x in line.strip().strip("|").split("|")]
    if len(c) < 7:
        continue
    biz, qid, q, kỳ, sql, res, gold = c[0], c[1], c[2], c[3], c[4], c[5], c[6]
    months = set(re.findall(r"(\d{4}-\d{2})-\d{2}", kỳ))
    miss = bool(months & MISSING)
    unsup = bool(UNSUP.search(q))
    r, g = num(res), num(gold)
    if res == "None" or r is None:
        diff = "NULL"
    elif g:
        diff = f"{(r - g) / abs(g) * 100:+.2f}%"
    else:
        diff = "?"
    tag = "THANG_THIEU" if miss else ("DANG_BI_LOAI" if unsup else "KHAC")
    rows.append((tag, qid[-6:], q[:55], kỳ[:42], res[:14], gold[:14], diff))

rows.sort()
from collections import Counter
print(Counter(r[0] for r in rows))
for r in rows:
    print(" | ".join(r))