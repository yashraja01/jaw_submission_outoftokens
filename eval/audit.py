"""Plausibility audit: check the OUTPUT, not the code.

Shape misclassification is the dominant scoring loss and no unit test catches it. Grouping answers
by shape and looking at the distribution does: a shape that clusters tightly except for two wild
outliers has two misrouted questions.
"""
import csv
import json
import pathlib
import statistics
import sys

if hasattr(sys.stdout, "reconfigure"):   # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

README_GOLD = {"HV-IC-0001": 2942400000.0, "HV-IC-0002": 1516600000.0, "HV-IC-0003": 90.19}
TOTAL_DELIVERED = 55_303_999_999
MAX_WORK = 2_000_000_000


def load():
    sub = {r["question_id"]: r["answer"] for r in csv.DictReader(open(ROOT / "submission.csv"))}
    log = {r["qid"]: r for r in json.load(open(ROOT / "build" / "derivations.json"))}
    return sub, log


def main():
    sub, log = load()
    problems = []

    print("=" * 96)
    print("README gold spot-check (outside the sample set)")
    print("=" * 96)
    ok = True
    for qid, gold in README_GOLD.items():
        got = float(sub[qid])
        good = abs(got - gold) < (0.005 if isinstance(gold, float) and gold < 100 else 0.5)
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {qid}  want={gold}  got={got}")
    print(f"  -> {'ALL PASS' if ok else 'REGRESSION'}")

    print("\n" + "=" * 96)
    print("distribution by shape")
    print("=" * 96)
    by = {}
    for qid, r in log.items():
        by.setdefault(r["shape"], []).append((qid, float(sub[qid]), r))
    print(f"{'shape':22s} {'n':>4s} {'fb':>3s} {'min':>15s} {'median':>15s} {'max':>15s}  outliers")
    for shape in sorted(by, key=lambda s: -len(by[s])):
        rows = by[shape]
        vals = [v for _, v, _ in rows]
        med = statistics.median(vals)
        fb = sum(1 for _, _, r in rows if r["source"] != "computed")
        # an outlier is >25x or <1/25 the shape median (money/days only; counts are small ints)
        outl = [q for q, v, _ in rows
                if med and (v > 25 * med or v < med / 25) and rows[0][2]["answer_type"] != "count"]
        print(f"{shape:22s} {len(rows):4d} {fb:3d} {min(vals):15,.2f} {med:15,.2f} "
              f"{max(vals):15,.2f}  {outl[:4]}")
        problems += [(shape, q, "distribution outlier") for q in outl]

    print("\n" + "=" * 96)
    print("hard plausibility checks")
    print("=" * 96)
    for qid, r in log.items():
        v, t = float(sub[qid]), r["answer_type"]
        if t == "money" and v > TOTAL_DELIVERED:
            problems.append((r["shape"], qid, f"exceeds total delivered value ({v:,.0f})"))
        if t == "percent" and not (0 <= v <= 100):
            problems.append((r["shape"], qid, f"percent out of range ({v})"))
        if t == "count" and (v < 0 or v != int(v) or v > 155):
            problems.append((r["shape"], qid, f"implausible count ({v})"))
        if t == "days" and not (0 < v < 6000):
            problems.append((r["shape"], qid, f"implausible day count ({v})"))
    print(f"  hard-check violations: {sum(1 for p in problems if 'outlier' not in p[2])}")
    for p in [p for p in problems if "outlier" not in p[2]][:15]:
        print("   ", p)

    print("\n" + "=" * 96)
    print("unresolved anchors (fallback rows)")
    print("=" * 96)
    fb = [r for r in log.values() if r["source"] != "computed"]
    reasons = {}
    for r in fb:
        reasons.setdefault(r["derivation"] or r["source"], []).append(r["qid"])
    for reason, qids in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(qids):4d}  {str(reason)[:80]:80s} {qids[:3]}")
    print(f"\n  total fallback rows: {len(fb)} / {len(log)}")

    print("\n" + "=" * 96)
    print("corpus scale checks")
    print("=" * 96)
    from qa.resolve import Facts
    f = Facts()
    print(f"  works                    {len(f.works)}   (want 155)")
    print(f"  clients with works       {len(f.clients)}   (want 28 names / 60 record ids)")
    print(f"  distinct managers        {len(f.managers)}")
    print(f"  sum of contract values   {sum(w['value'] for w in f.works):,}   (want 55,303,999,999)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
