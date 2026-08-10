"""Submission format validator. Run as the last step of every tier."""
import csv, json, math, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DS = ROOT / "dataset"


def validate(sub_path=ROOT / "submission.csv", strict_order=True):
    errs, warns = [], []
    qs = json.load(open(DS / "questions.json", encoding="utf-8"))["questions"]
    want = [q["qid"] for q in qs]
    atype = {q["qid"]: q["answer_type"] for q in qs}

    tmpl = [r["question_id"] for r in csv.DictReader(open(DS / "sample_submission.csv"))]
    if tmpl != want:
        warns.append("template order differs from questions.json order")

    raw = open(sub_path, newline="", encoding="utf-8").read()
    if "﻿" in raw:
        errs.append("file contains a BOM")
    rows = list(csv.reader(raw.splitlines()))
    if not rows:
        return ["empty file"], warns

    if rows[0] != ["question_id", "answer"]:
        errs.append(f"header is {rows[0]!r}, expected ['question_id','answer']")
    data = rows[1:]
    if len(data) != 371:
        errs.append(f"{len(data)} data rows, expected 371")

    got = [r[0] for r in data if r]
    if strict_order and got != tmpl:
        missing, extra = set(want) - set(got), set(got) - set(want)
        if missing:
            errs.append(f"missing qids: {sorted(missing)[:5]} ({len(missing)})")
        if extra:
            errs.append(f"unknown qids: {sorted(extra)[:5]} ({len(extra)})")
        if not missing and not extra:
            errs.append("row order does not match sample_submission.csv")
    if len(set(got)) != len(got):
        errs.append("duplicate qids present")

    for r in data:
        if len(r) != 2:
            errs.append(f"{r[0] if r else '?'}: {len(r)} columns, expected 2"); continue
        qid, a = r[0], r[1]
        if a.strip() != a:
            errs.append(f"{qid}: padded whitespace {a!r}")
        if a == "" or a.lower() in {"nan", "none", "null", "inf", "-inf"}:
            errs.append(f"{qid}: empty/non-numeric {a!r}"); continue
        if any(c in a for c in ",₹\"'%") or "e" in a.lower() or "INR" in a:
            errs.append(f"{qid}: illegal characters in {a!r}"); continue
        try:
            v = float(a)
        except ValueError:
            errs.append(f"{qid}: not a number {a!r}"); continue
        if math.isnan(v) or math.isinf(v):
            errs.append(f"{qid}: nan/inf"); continue
        t = atype.get(qid)
        if t in ("money", "count", "days") and "." in a:
            errs.append(f"{qid}: {t} must be an integer, got {a!r}")
        if t == "count" and v < 0:
            errs.append(f"{qid}: negative count {a!r}")
        if t == "days" and v <= 0:
            errs.append(f"{qid}: non-positive day count {a!r}")
        if t == "percent" and not (0 <= v <= 100):
            errs.append(f"{qid}: percent out of range {a!r}")
        # money is signed: mean_minus_median questions state "negative if avg dips"
        if t == "money" and not (0 < abs(v) <= 55_303_999_999):
            errs.append(f"{qid}: money outside corpus range {a!r}")
    return errs, warns


if __name__ == "__main__":
    p = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "submission.csv"
    e, w = validate(p)
    for x in w:
        print("  WARN ", x)
    for x in e[:40]:
        print("  ERROR", x)
    print(f"\n{p.name}: {len(e)} errors, {len(w)} warnings — {'PASS' if not e else 'FAIL'}")
    sys.exit(1 if e else 0)
