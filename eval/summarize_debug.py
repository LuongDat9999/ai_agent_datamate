import collections
import json
import sys

debug_path = sys.argv[1]
gold_path = sys.argv[2]

rows = [json.loads(l) for l in open(debug_path, encoding="utf8") if l.strip()]
gold = {}
for l in open(gold_path, encoding="utf8"):
    if l.strip():
        g = json.loads(l)
        gold[g["id"]] = g.get("answer")


def out_val(o):
    return o.get("answer") if isinstance(o, dict) else o


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def same(a, b, answer_type=None):
    if isinstance(b, list) or isinstance(a, list):
        if not (isinstance(a, list) and isinstance(b, list)) or len(a) != len(b):
            return False
        return all(same(x, y, answer_type) for x, y in zip(a, b))
    na, nb = num(a), num(b)
    if na is not None and nb is not None:
        tolerance = 0.01 if answer_type == "percent" else abs(nb) * 1e-6
        return abs(na - nb) <= tolerance
    return str(a).strip() == str(b).strip()


print("Tổng:", len(rows))
print("decision:", dict(collections.Counter(r.get("decision") for r in rows)))
print("path:", dict(collections.Counter(r.get("path") for r in rows)))
skip_counts = collections.Counter(
    r.get("resolver_skip_reason") for r in rows if r.get("resolver_skip_reason")
)
print("\nTheo skip_reason:")
for reason, count in skip_counts.most_common():
    print(f"  {reason}: {count}")

stat = collections.defaultdict(collections.Counter)
business_stat = collections.defaultdict(collections.Counter)
wrong = []
nogold = 0
for r in rows:
    key = (r.get("business_id"), r.get("path"))
    if r.get("decision") != "answer":
        stat[key]["abstain"] += 1
        business_stat[r.get("business_id")]["abstain"] += 1
        continue
    if r["id"] not in gold:
        nogold += 1
        business_stat[r.get("business_id")]["no_gold"] += 1
        continue
    output = r.get("output") or {}
    ok = same(out_val(output), gold[r["id"]], output.get("type"))
    stat[key]["đúng" if ok else "sai"] += 1
    business_stat[r.get("business_id")]["đúng" if ok else "sai"] += 1
    if not ok:
        wrong.append((r["id"][-8:], r.get("business_id"), r.get("path"),
                      r["question"][:55], out_val(r.get("output")), gold[r["id"]]))

print("\nTheo (business, path):")
for k, v in sorted(stat.items(), key=lambda x: str(x[0])):
    print(" ", k, dict(v))
print("\nTheo business:")
for business_id, values in sorted(business_stat.items(), key=lambda item: str(item[0])):
    total = sum(values.values())
    score = (values["đúng"] - values["sai"]) / total if total else 0
    print(f"  {business_id}: {dict(values)} PA={score:+.3f}")
c = sum(v["đúng"] for v in stat.values())
w = sum(v["sai"] for v in stat.values())
print(f"\nĐÚNG={c}  SAI={w}  PA={(c - w) / len(rows):+.3f}  (câu answer không có gold: {nogold})")
print("\nCâu sai:")
for x in wrong[:60]:
    print(" ", x)
