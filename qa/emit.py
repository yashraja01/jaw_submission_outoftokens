"""Write submission.csv from a {qid: value} dict, coercing to the required per-type format.

Any qid the solver could not answer falls back to a type-appropriate placeholder — never blank,
never 0, because both score exactly zero under the continuous scorer.
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
        # signed: the mean_minus_median shape is explicitly "negative if avg dips"
        n = _int(f)
        return str(n) if 0 < abs(n) <= 55_303_999_999 else None
    if atype == "count":
        n = _int(f)
        return str(n) if n >= 0 else None
    if atype == "days":
        n = _int(f)
        return str(n) if n > 0 else None
    return None


def write(answers, path=ROOT / "submission.csv", log=None):
    qs = json.load(open(DS / "questions.json", encoding="utf-8"))["questions"]
    order = [r["question_id"] for r in csv.DictReader(open(DS / "sample_submission.csv"))]
    atype = {q["qid"]: q["answer_type"] for q in qs}

    rows, filled = [], 0
    for qid in order:
        t = atype[qid]
        s = coerce(answers.get(qid), t)
        if s is None:
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
    return filled, len(rows)


if __name__ == "__main__":
    n, tot = write({})
    print(f"skeleton written: {n} solved, {tot} rows")
