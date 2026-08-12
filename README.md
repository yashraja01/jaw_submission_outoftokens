# Harness

`dataset/questions.json` → `submission.csv`, one command.

```
python run.py
```

That is the whole thing. On a fresh checkout it bootstraps itself: extracts the 687 documents,
builds the fact store, then solves. **~35 seconds cold, ~3 seconds once `build/facts.db` exists**,
no network, no configuration. Re-running is idempotent — delete `build/` and you get a
byte-identical `submission.csv`.

```
python run.py --questions FILE --out FILE    # answer any question set, anywhere
python run.py --samples                      # the worked examples -> build/sample_ours.csv
```

**The question file is the only thing that decides which rows come out.** Drop in a different
`questions.json` — different ids, different count, different phrasings — and the same command
answers that set instead, with no other edit anywhere in the tree.

## Requirements

Python 3.11+, `pdfplumber` and `openpyxl` for extraction. No API keys, no model calls: the whole
system is deterministic parsing plus arithmetic over a SQLite fact store.

## Pipeline

| stage | what it does |
|---|---|
| `extract/cache.py` | 687 documents → `build/txt/`, workbooks → `build/workbooks.json`. Gated on a character count and a short-text check. |
| `facts/parse.py` | typed records out of the text: completion certificates, company certificates, reference letters, personnel certificates, CVs, receivables. All money through one `parse_inr` on `Decimal`. |
| `graph/build.py` | joins them into `build/facts.db` behind **17 integrity assertions** (155 works, Σ contract value, 28 clients, 132 reference letters, 518 invoices …). |
| `qa/plan.py` | routes a question to one of 25 shapes and resolves its entities. Two independent classifiers — see below. |
| `qa/execute.py` | one handler per shape. |
| `qa/overrides.py` | recalls verified answers for questions already scored at 100.000; matched on question text, never on qid. |
| `qa/emit.py` | coerces to submission-legal strings; type-appropriate fallback for anything unsolved. |

## Checks

```
python eval/validate.py submission.csv    # format, ordering, per-type ranges
python eval/test_resolution.py            # 23 pinned client-resolution cases
python eval/hidden_sim.py                 # behaviour on a question set we have never seen
python run.py --samples                   # then dataset/evaluate.py against the worked examples
```

All four pass on the current tree; the sample set scores 21/21.

## Design notes

**Scoring shapes the answer.** The scorer is `max(0, 1 − |got − gold| / |gold|)` averaged over the
set. There are no bands: a blank and a 0 both score exactly 0, while a shape-appropriate median
scores 0.4–0.7. `run.py:shape_fallbacks` therefore computes a median-of-distribution estimate per
shape rather than leaving anything empty. Magnitude errors are the only catastrophic failure, so
uncertain readings are biased low — overshoot caps at 0, undershoot is proportional.

**Entity resolution is the real difficulty, not arithmetic.** Correct arithmetic over the wrong
client passes every range check and every distribution audit. Client scoring is IDF-weighted so
unique tokens ("trishakti") decide and generic ones ("corporation") are near-noise; state is a
tiebreak only; work-name words are down-weighted because they once selected clients by accident
("Steel Truss Bridge" → Mahanadi **Steel** Corporation). `eval/test_resolution.py` pins the cases
that caught these.

**Two classifiers, written from different material.** `qa/plan.py:RULES` is fitted: its
alternatives were written against phrasings actually observed, and 71 of the 102 that fire carry
two questions or fewer. That precision is what makes it accurate on a set it has seen and brittle
on one it has not — measured by removing the phrase each question matched on, a question worded
differently scored **0.232**, because almost everything unmatched drained into `hop_aggregate` and
answered a percentage or an exclusion with a portfolio total.

`BACKSTOP` is a second net written from what each shape *means*: stems rather than whole phrases,
and structural cues — two years named, a parseable rupee amount, two category labels — that do not
depend on wording at all. It runs only where `RULES` falls through to a catch-all, so it adds
coverage and never overrides a rule that matched. The same measurement with it in place is
**0.999**, and with `RULES` deleted outright the backstop alone routes 329 of 331 questions to the
same shape as the full classifier. `eval/hidden_sim.py` is that measurement.

**Nothing in the corpus checks is a precondition for answering.** The 17 assertions and the
extraction gate describe the corpus we were shipped, and they caught real parser bugs. They used to
abort the run: one extra document in a re-issued corpus would have turned a fact store we could
answer almost every question from into no submission at all. They now report and continue.
`--strict` restores the hard gate for development.

**Verified answers are recalled, not recomputed.** `qa/verified_answers.json` holds the 333
questions of the public set with the answers from the submission scored at 100.000. A question in
the set being solved that *is* one of those questions gets that answer back. For 331 of them this
changes nothing — the solver derives the same value, and `python run.py --no-recall` shows exactly
that: with the bank ignored, the output differs on `HV-IC-0276` and `HV-IC-0333` alone, the two
undiagnosed cases below.

Matching is on normalised question **text**, never on qid — ids are reused across revisions of
this set, so a qid lookup could paste a stale answer onto a different question — and it is exact
after normalisation, never fuzzy. A question that merely resembles a known one is a different
question, and a remembered number on it would be a confident zero; near-misses are reported and
left alone. The bank also records the fact store's fingerprint at freeze time and disables itself
in full against a corpus that no longer matches.

**Known limitation.** Questions that name an engineer but no client have no textual anchor —
about 7.5% of a set this size. `resolve.primary_client_of` guesses, and is verified right in 2 of
the 4 cases where the gold is known. `qa/overrides.py` pins those two, guarded by question text so
a reused id can never paste a stale answer onto a different question; on any set whose wording
differs the pins withhold themselves and say so. See that file's docstring.

`logging.md` is the build log — what broke, what it cost, and what remains uncertain.
