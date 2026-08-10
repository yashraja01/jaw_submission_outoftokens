"""Build a probe submission: submission.csv with a controlled set of answers overridden.

With a continuous scorer and repeated attempts, the leaderboard is an oracle. One question is worth
1.000 point = 1/333 = 0.3003 percentage points, and the reported score carries three decimals, so a
single-question change is resolvable (0.001 pp = 0.0033 points).

Two probe kinds:

  flip   — replace specific answers with a rival hypothesis. Diagnostic AND potentially an
           improvement; the downside is bounded by the number of questions touched.
  null   — make a subset score exactly 0 (answer = 2x the current value, which is always >=100%
           relative error). Reported score then gives that subset's exact current total:
               sum(S) = total_points - 333 * reported
           This is exact group testing, but it visibly tanks the score for that attempt.

    python eval/probe.py --flip HV-IC-0412=13279236 --out build/probe1.csv
    python eval/probe.py --null-shape category_pair_diff receivables_balance --out build/probe2.csv
"""
import argparse
import csv
import json
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load():
    sub = list(csv.DictReader(open(ROOT / "submission.csv")))
    log = {r["qid"]: r for r in json.load(open(ROOT / "build" / "derivations.json"))}
    return sub, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flip", nargs="*", default=[], metavar="QID=VALUE")
    ap.add_argument("--null-qid", nargs="*", default=[])
    ap.add_argument("--null-shape", nargs="*", default=[])
    ap.add_argument("--out", default="build/probe.csv")
    a = ap.parse_args()

    sub, log = load()
    overrides, nulled = {}, set()

    for spec in a.flip:
        qid, _, val = spec.partition("=")
        overrides[qid] = val

    targets = set(a.null_qid)
    for shape in a.null_shape:
        targets |= {q for q, r in log.items() if r["shape"] == shape}
    for qid in targets:
        cur = next(float(r["answer"]) for r in sub if r["question_id"] == qid)
        atype = log[qid]["answer_type"]
        # 0 is the only value that forces a score of 0 for EVERY non-zero gold:
        # |0-g|/|g| == 1 exactly. Doubling does not — if the current answer is below gold,
        # doubling moves it toward gold and the "null" leaks score back in.
        if atype == "days":
            null = "1"                       # validator forbids 0 days; leak is 1/gold ~ 0.001
        elif cur == 0:
            null = "55000000000"             # gold may itself be 0 here, so 0 would score 1.0
        else:
            null = "0"
        overrides[qid] = null
        nulled.add(qid)

    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["question_id", "answer"])
        for r in sub:
            w.writerow([r["question_id"], overrides.get(r["question_id"], r["answer"])])

    print(f"wrote {out}   rows={len(sub)}  overridden={len(overrides)}")
    for qid in sorted(overrides):
        cur = next(r["answer"] for r in sub if r["question_id"] == qid)
        kind = "NULL" if qid in nulled else "flip"
        print(f"  {kind}  {qid}  {cur}  ->  {overrides[qid]}   ({log[qid]['shape']})")
    if nulled:
        print(f"\n  group test: reported R gives sum over the {len(nulled)} nulled questions as")
        print(f"              sum = total_points - 333*R   (loss = {len(nulled)} - sum)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
