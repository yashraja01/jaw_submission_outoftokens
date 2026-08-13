"""Score a submission against the visible-set answers, under the tie-breaker's tolerances.

Development only. Nothing under `pipeline/` imports this file or the answers it reads — the graded
run has no gold to look at, and a pipeline that could reach one would not be measuring anything.

    python dev/score.py --submission submission.csv [--log build/run_log.json]

Tolerances are the ones the tie-breaker states:
    money    within max(1 rupee, 0.5% of the correct value)
    count    exact
    percent  within 0.05
"""
import argparse
import collections
import csv
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
GOLD = ROOT / "dev" / "gold_visible.json"


def within(got, want, answer_type):
    if got is None or got == "":
        return False
    try:
        g, w = float(got), float(want)
    except (TypeError, ValueError):
        return False
    if answer_type == "money":
        return abs(g - w) <= max(1.0, 0.005 * abs(w))
    if answer_type == "percent":
        return abs(g - w) <= 0.05
    return abs(g - w) < 1e-9                                # count, days: exact


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission", default="submission.csv")
    ap.add_argument("--log", default="build/run_log.json")
    ap.add_argument("--show", type=int, default=25, help="how many misses to print")
    a = ap.parse_args()

    gold = {r["qid"]: r for r in json.loads(GOLD.read_text(encoding="utf-8"))["answers"]}
    sub = {r["question_id"]: r["answer"]
           for r in csv.DictReader(open(a.submission, encoding="utf-8"))}
    log = {}
    p = pathlib.Path(a.log)
    if p.exists():
        log = {r["qid"]: r for r in json.loads(p.read_text(encoding="utf-8"))}

    by_type = collections.Counter()
    by_type_ok = collections.Counter()
    by_shape = collections.Counter()
    by_shape_ok = collections.Counter()
    misses = []
    scored = 0
    for qid, g in gold.items():
        if qid not in sub:
            continue
        scored += 1
        at = g["answer_type"]
        ok = within(sub[qid], g["answer"], at)
        by_type[at] += 1
        shape = (log.get(qid) or {}).get("shape", "?")
        by_shape[shape] += 1
        if ok:
            by_type_ok[at] += 1
            by_shape_ok[shape] += 1
        else:
            misses.append((qid, at, shape, sub[qid], g["answer"], log.get(qid, {})))

    total_ok = sum(by_type_ok.values())
    print(f"exact-match: {total_ok}/{scored} = {total_ok / max(scored, 1):.4f}")
    for t in sorted(by_type):
        print(f"   {t:8s} {by_type_ok[t]:3d}/{by_type[t]:3d}")
    print("\nby shape (rules classifier's reading):")
    for s, n in by_shape.most_common():
        print(f"   {s:24s} {by_shape_ok[s]:3d}/{n:3d}")
    print(f"\n{len(misses)} misses; first {a.show}:")
    for qid, at, shape, got, want, rec in misses[:a.show]:
        print(f"  {qid} [{at}/{shape}] got={got} want={want}")
        if rec:
            print(f"      plan: {rec.get('plan')}")
            print(f"      why : {rec.get('derivation')}")
            print(f"      who : {rec.get('entities')}")
            if rec.get("errors"):
                print(f"      err : {rec['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
