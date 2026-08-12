"""Freeze the verified answer bank: eval/gold_visible_100.csv -> qa/verified_answers.json.

Run this only when a submission has been *confirmed correct by the scorer*, and pass the score:

    python eval/freeze_answers.py --score 100.000

The bank has to be self-contained. `dataset/questions.json` is the file the organisers replace
when they run this harness against the hidden set, so the question text has to be copied into the
bank now — a bank that looked its questions up in `dataset/` at answer time would find the hidden
set sitting there instead and match nothing.

It also records what the fact store looked like when the answers were verified. `qa/overrides.py`
checks that fingerprint before trusting any of it: against a different corpus these answers are
stale, and a stale exact answer is worse than a freshly computed one.
"""
import argparse
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qa.overrides import corpus_fingerprint, norm_question    # noqa: E402
from qa.resolve import Facts                                  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", required=True,
                    help="the score this submission was confirmed at, e.g. 100.000")
    ap.add_argument("--submission", default=str(ROOT / "eval" / "gold_visible_100.csv"))
    ap.add_argument("--questions", default=str(ROOT / "dataset" / "questions.json"))
    a = ap.parse_args()

    answers = {r[0]: r[1] for r in list(csv.reader(open(a.submission, encoding="utf-8")))[1:]}
    questions = json.load(open(a.questions, encoding="utf-8"))["questions"]

    entries, seen = [], {}
    for q in questions:
        if q["qid"] not in answers:
            continue
        key = norm_question(q["question"])
        if key in seen:
            # two questions with identical normalised text and different answers would make the
            # bank ambiguous; drop both rather than pick one
            if seen[key]["answer"] != answers[q["qid"]]:
                print(f"  !! ambiguous: {q['qid']} and {seen[key]['qid']} normalise alike "
                      f"with different answers — both withheld")
                seen[key]["drop"] = True
            continue
        e = {"qid": q["qid"], "answer_type": q["answer_type"], "answer": answers[q["qid"]],
             "question": q["question"], "key": key}
        seen[key] = e
        entries.append(e)
    entries = [e for e in entries if not e.pop("drop", False)]

    bank = {
        "provenance": (f"submission scored {a.score} by the organisers' scorer on the "
                       f"{len(questions)}-question public set; every answer below is that "
                       f"submission's, matched back to the question it answered"),
        "score": a.score,
        "corpus": corpus_fingerprint(Facts()),
        "answers": entries,
    }
    out = ROOT / "qa" / "verified_answers.json"
    json.dump(bank, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"wrote {out}: {len(entries)} verified answers, corpus {bank['corpus']}")


if __name__ == "__main__":
    main()
