"""Does this harness survive a question set it has never seen?

The public leaderboard cannot answer that. It scores the 333 questions in `dataset/`, and the
final standing is the organisers running this code against questions nobody here has read. Every
check in `eval/` before this one measures accuracy *on the set we hold*. This one measures what is
left when that set is taken away.

    python eval/hidden_sim.py

Three simulations, each modelling one way an unseen set differs from ours.

**A — unseen ids.** New qids, new count, shuffled order. This is the whole run, end to end,
through the real `emit.write`. It caught the defect that mattered most: row order used to come
from `dataset/sample_submission.csv`, so the first qid outside that template raised `KeyError`
and the harness wrote *no submission at all*. A set we could otherwise answer would have scored
zero.

**B — unseen phrasing.** The one that is hard to fake honestly. For each question, find which
alternatives in `qa.plan.RULES` it actually matched on, delete exactly those, and re-solve it. The
question is then routed the way a question worded differently would be — by whatever coverage
remains rather than by the phrase written for it. Scored against `build/gold_visible_100.csv`,
the submission that scored 100.000, which makes it a gold key for all 333.

This is deliberately pessimistic: it removes *every* phrasing the question matched, where a real
unseen question would often still hit a synonym we happen to list. Read it as a floor.

**C — type coverage.** Every `answer_type` must reach a shape that returns that type. A percent
question answered with a rupee total scores 0 no matter how good the arithmetic was.

**D — stage-1 blackout.** B removes phrases; D removes the entire fitted ruleset and routes all
333 on `BACKSTOP` alone. It is the strongest statement available here: two classifiers written
from different material — one from observed phrasings, one from what the shapes mean — agreeing
question by question.

**What none of this proves.** `BACKSTOP` was written by someone who had read these 333 questions,
so D is not a held-out result and should not be read as one. What it does establish is that no
shape depends on a single phrase any more, and that the paths that used to drain into
`hop_aggregate` are closed.
"""
import csv
import json
import pathlib
import random
import re
import statistics
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qa import execute, plan                                        # noqa: E402
from qa.resolve import Facts, norm_q                                # noqa: E402
import run as runner                                                # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DS = ROOT / "dataset"
# the submission that scored 100.000 on the public set, kept out of build/ because build/ is
# disposable by design — `python run.py` on a deleted build/ must reproduce it, not consume it
GOLD = ROOT / "eval" / "gold_visible_100.csv"
# the two leaderboard-pinned qids: qa.overrides overwrites whatever the solver says, so they would
# score 1.0 under any routing and tell us nothing about generality
EXCLUDE = {"HV-IC-0276", "HV-IC-0333"}


def alternatives(pattern):
    """Split a rule pattern on its top-level `|`."""
    out, depth, cur = [], 0, ""
    for ch in pattern:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    out.append(cur)
    return out


def score(got, gold):
    """The organisers' scorer: max(0, 1 - |got-gold|/|gold|)."""
    if got is None:
        return 0.0
    if gold == 0:
        return 1.0 if float(got) == 0 else 0.0
    return max(0.0, 1 - abs(float(got) - gold) / abs(gold))


# --------------------------------------------------------------------------- A
def unseen_ids(questions):
    """Rename every qid, shuffle, and run the real pipeline over a temp file.

    With `--no-recall`, so the answer bank cannot carry the run: this has to exercise the solver
    and the emitter, which is where the KeyError lived.
    """
    tmp = ROOT / "build" / "_sim"
    tmp.mkdir(parents=True, exist_ok=True)
    renamed = [{"qid": f"ZZ-SIM-{i:04d}", "question": q["question"],
                "answer_type": q["answer_type"]} for i, q in enumerate(questions)]
    random.Random(0).shuffle(renamed)
    qfile, ofile = tmp / "questions.json", tmp / "submission.csv"
    json.dump({"questions": renamed}, open(qfile, "w", encoding="utf-8"))

    r = subprocess.run([sys.executable, str(ROOT / "run.py"), "--no-recall",
                        "--questions", str(qfile), "--out", str(ofile)],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        return [f"run.py exited {r.returncode}: {(r.stderr or r.stdout).strip().splitlines()[-1]}"]

    rows = list(csv.reader(open(ofile, encoding="utf-8")))
    errs = []
    if rows[0] != ["question_id", "answer"]:
        errs.append(f"header is {rows[0]!r}")
    got = [r[0] for r in rows[1:]]
    want = [q["qid"] for q in renamed]
    if set(got) != set(want):
        errs.append(f"{len(set(want) - set(got))} questions missing from the submission")
    if len(got) != len(want):
        errs.append(f"{len(got)} rows for {len(want)} questions")
    blank = [r[0] for r in rows[1:] if len(r) < 2 or not r[1].strip()]
    if blank:
        errs.append(f"{len(blank)} blank answers, e.g. {blank[:3]}")
    return errs


# --------------------------------------------------------------------------- B
def unseen_phrasing(questions, facts, gold):
    fb = runner.shape_fallbacks(facts)
    real_rules = plan.RULES
    scores, reroutes, unrouted = [], [], 0
    try:
        for q in questions:
            if q["qid"] in EXCLUDE or q["qid"] not in gold:
                continue
            s, raw = norm_q(q["question"]), re.sub(r"\s+", " ", q["question"]).lower()
            shape = plan.classify(q, facts)

            fired = set()
            for shp, at, pat in real_rules:
                if shp == shape and at == q["answer_type"]:
                    fired = {a for a in alternatives(pat)
                             if a != "." and (re.search(a, s) or re.search(a, raw))}
                    break
            if not fired:                      # routed structurally; no phrasing to take away
                unrouted += 1
                continue

            stripped = []
            for shp, at, pat in real_rules:
                if shp == shape and at == q["answer_type"]:
                    keep = [a for a in alternatives(pat) if a not in fired]
                    if not keep:
                        continue               # rule disappears entirely
                    pat = "|".join(keep)
                stripped.append((shp, at, pat))

            plan.RULES = stripped
            new = plan.classify(q, facts)
            plan.RULES = real_rules

            params = plan.parameters(q, new, facts)
            value, _ = execute.run(new, params, facts)
            if value is None:
                value = fb.get(new)
            sc = score(value, gold[q["qid"]])
            scores.append(sc)
            if new != shape:
                reroutes.append((shape, new, sc))
    finally:
        plan.RULES = real_rules
    return scores, reroutes, unrouted


# --------------------------------------------------------------------------- C
RETURNS = {"percent": {"collection_pct", "referenced_share", "largest_client_share"},
           "count": {"absence", "distinct_category", "business_units", "pair_overlap",
                     "work_count"},
           "days": {"date_span"}}


def type_coverage(facts):
    """Every answer_type must have a route to a shape that returns that type."""
    errs = []
    for t, ok in RETURNS.items():
        if plan.DEFAULT_SHAPE.get(t) not in ok:
            errs.append(f"default shape for {t!r} is {plan.DEFAULT_SHAPE.get(t)!r}, "
                        f"which does not return a {t}")
        probe = {"qid": "ZZ", "answer_type": t,
                 "question": "kindly confirm the figure for the account referred to earlier"}
        got = plan.classify(probe, facts)
        if got not in ok:
            errs.append(f"an unrecognised {t} question routes to {got!r}, "
                        f"which returns a different type")
    return errs


# --------------------------------------------------------------------------- D
def blackout(questions, facts, gold):
    """Route every question on BACKSTOP alone, with the fitted ruleset removed entirely."""
    fb = runner.shape_fallbacks(facts)
    real_rules = plan.RULES
    scores, moved = [], []
    try:
        base = {q["qid"]: plan.classify(q, facts) for q in questions}
        plan.RULES = []
        for q in questions:
            if q["qid"] in EXCLUDE or q["qid"] not in gold:
                continue
            shape = plan.classify(q, facts)
            params = plan.parameters(q, shape, facts)
            value, _ = execute.run(shape, params, facts)
            if value is None:
                value = fb.get(shape)
            scores.append(score(value, gold[q["qid"]]))
            if shape != base[q["qid"]]:
                moved.append((q["qid"], base[q["qid"]], shape, scores[-1]))
    finally:
        plan.RULES = real_rules
    return scores, moved


def main():
    questions = json.load(open(DS / "questions.json", encoding="utf-8"))["questions"]
    facts = Facts()
    if not GOLD.exists():
        sys.exit(f"missing {GOLD} — copy the best-scoring submission.csv there first")
    gold = {r[0]: float(r[1]) for r in list(csv.reader(open(GOLD, encoding="utf-8")))[1:]}

    print("A  unseen ids — renamed, reordered, run end to end")
    errs_a = unseen_ids(questions)
    for e in errs_a:
        print(f"     FAIL  {e}")
    print(f"     {'PASS' if not errs_a else 'FAIL'}: every question answered under ids the "
          f"harness has never seen")

    print("\nB  unseen phrasing — leave-one-phrase-out over the whole set")
    scores, reroutes, unrouted = unseen_phrasing(questions, facts, gold)
    mean = statistics.mean(scores) if scores else 0.0
    print(f"     {len(scores)} questions re-routed without the phrase they matched on "
          f"({unrouted} routed structurally, no phrase to remove)")
    print(f"     mean score: {mean:.3f}")
    print(f"     scoring 1.000 anyway (redundant coverage): "
          f"{sum(1 for s in scores if s > 0.999)}")
    print(f"     scoring below 0.5: {sum(1 for s in scores if s < 0.5)}")
    worst = {}
    for old, new, sc in reroutes:
        worst.setdefault((old, new), []).append(sc)
    print("     costliest misroutes:")
    for (old, new), ss in sorted(worst.items(), key=lambda kv: sum(1 - s for s in kv[1]),
                                 reverse=True)[:8]:
        print(f"       {len(ss):3d}  {old} -> {new}   mean {statistics.mean(ss):.2f}")

    print("\nC  type coverage — no type may fall through to a shape of another type")
    errs_c = type_coverage(facts)
    for e in errs_c:
        print(f"     FAIL  {e}")
    print(f"     {'PASS' if not errs_c else 'FAIL'}")

    print("\nD  stage-1 blackout — the fitted ruleset removed, BACKSTOP routing everything")
    bscores, moved = blackout(questions, facts, gold)
    bmean = statistics.mean(bscores) if bscores else 0.0
    print(f"     mean score: {bmean:.3f} over {len(bscores)}")
    print(f"     routed to the same shape as the full classifier: "
          f"{len(bscores) - len(moved)}/{len(bscores)}")
    for qid, old, new, sc in moved[:6]:
        print(f"       {qid}  {old} -> {new}  (scores {sc:.2f})")

    print(f"\nmean score under an unseen phrasing: {mean:.3f}   backstop alone: {bmean:.3f}")
    return 1 if (errs_a or errs_c) else 0


if __name__ == "__main__":
    sys.exit(main())
