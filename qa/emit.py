"""Write submission.csv from a {qid: value} dict, coercing to the required per-type format.

Any qid the solver could not answer falls back to a type-appropriate placeholder — never blank,
never 0, because both score exactly zero under the continuous scorer.

**The question file is the only authority on which rows exist.** This module used to take its row
order from `dataset/sample_submission.csv` and its answer types from `questions.json`. Handed a
question set whose ids it had not seen, it raised `KeyError` on the first row and wrote nothing —
so the harness scored 0 on any set the template did not already describe. Order now comes from the
questions themselves; the template is consulted only to preserve its ordering when it happens to
cover exactly the same ids.
"""
import csv, json, math, pathlib
from decimal import Decimal, ROUND_HALF_UP

ROOT = pathlib.Path(__file__).resolve().parent.parent
DS = ROOT / "dataset"

# Tier-0 placeholders. Deliberately biased low: overshoot caps at 0, undershoot is proportional.
FALLBACK = {"money": 600_000_000, "percent": 50.0, "count": 3, "days": 900}


def _int(v):
    return int(Decimal(str(v)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def coerce(value, atype):
    """Return a submission-legal string, or None if the value is unusable."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    if atype == "percent":
        return f"{min(100.0, max(0.0, round(f, 2))):.2f}"
    if atype == "money":
        # Signed, and zero is a legitimate answer: a single-work client has mean == median, so
        # mean_minus_median is exactly 0. The scorer requires an exact 0 when gold is 0, so
        # rejecting it here (and substituting the placeholder) turned a 1.0 into a 0.
        n = _int(f)
        return str(n) if abs(n) <= 55_303_999_999 else None
    if atype == "count":
        n = _int(f)
        return str(n) if n >= 0 else None
    if atype == "days":
        n = _int(f)
        return str(n) if n > 0 else None
    return None


def submission_order(questions):
    """The qids to emit, in the order to emit them.

    Row order does not affect scoring, but matching the shipped template makes a diff against it
    readable. So: the template's order when it describes exactly this set, and the question file's
    own order otherwise — which is the case for any set we have not seen before.
    """
    order = [q["qid"] for q in questions]
    tmpl_path = DS / "sample_submission.csv"
    if tmpl_path.exists():
        try:
            tmpl = [r["question_id"] for r in csv.DictReader(open(tmpl_path, encoding="utf-8"))]
        except (OSError, csv.Error, KeyError):
            return order
        if set(tmpl) == set(order) and len(tmpl) == len(order):
            return tmpl
    return order


def write(answers, path=ROOT / "submission.csv", log=None, questions=None):
    if questions is None:
        questions = json.load(open(DS / "questions.json", encoding="utf-8"))["questions"]
    order = submission_order(questions)
    atype = {q["qid"]: q.get("answer_type") for q in questions}

    rows, filled, substituted = [], 0, []
    for qid in order:
        # an unrecognised answer_type is treated as money: it is 80% of every set seen so far, and
        # a wrong-but-numeric row scores better than a missing one
        t = atype.get(qid) if atype.get(qid) in FALLBACK else "money"
        s = coerce(answers.get(qid), t)
        if s is None:
            # the solver produced a value the format rejected — always worth surfacing, it once
            # silently replaced three legitimate zeros with the placeholder
            if answers.get(qid) is not None:
                substituted.append((qid, answers[qid]))
            s = coerce(FALLBACK[t], t)
        else:
            filled += 1
        rows.append((qid, s))

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["question_id", "answer"])
        w.writerows(rows)

    if log is not None:
        json.dump(log, open(ROOT / "build" / "derivations.json", "w"), indent=1, default=str)
    return filled, len(rows), substituted


if __name__ == "__main__":
    n, tot, _ = write({})
    print(f"skeleton written: {n} solved, {tot} rows")
