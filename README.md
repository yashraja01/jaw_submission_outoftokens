# Bid intelligence over a document estate

A pipeline that reads a directory of PDFs and spreadsheets, builds a queryable fact store out of
them, and answers numerical questions about the business they describe.

```bash
pip install -r requirements.txt
./setup.sh
./run.sh --docs /path/to/documents --questions /path/to/questions.json --out submission.csv
```

`run.sh` does everything: walks the tree, extracts and classifies every document, joins the records
into a fact store, reads each question into an executable plan, runs the plan in exact arithmetic
and writes the CSV. It needs the three paths and nothing else.

---

## How it answers a question

The core decision is that **the language model never sees a number and never returns one.**

Scoring is exact match — money within 0.5%, a count exactly, a percentage within 0.05 — and a
typical question totals nine or nineteen ten-digit rupee figures read out of separate certificates.
That is not work to hand to a language model. What *is* hard for code, and easy for a model, is
reading "what's left once we set the water treatment side aside" and knowing it means an exclusion.

So the model is a semantic parser. It receives the question and the estate's own vocabulary — the
real client names, engineer names and category labels, read out of the fact store at run time — and
returns a **plan**:

```json
{"anchor_client": "Irrigation & Waterways Dept, Govt of Rajasthan",
 "left":  {"source": "works", "scope": "client", "agg": "sum_value",
           "categories_not_in": ["water treatment"]},
 "right": {"source": "none"},
 "combine": "left"}
```

The plan is validated against a fixed vocabulary of sources, scopes, aggregates and filters, the
entities it mentions are resolved against the corpus (never accepted as free text), and then it is
executed in `Decimal` over rows parsed from the documents. Every rupee of arithmetic happens in
Python.

A second, model-free planner reads the same question by its grammar and emits the same kind of
plan. Both are executed. Where they agree, that is the answer. Where they disagree, see
[Who wins](#who-wins-when-the-planners-disagree).

### The stages

| stage | file | what it does |
|---|---|---|
| ingest | `pipeline/ingest.py` | walks `--docs` recursively; PyMuPDF for PDFs, openpyxl for workbooks; classifies every file **by its own text**, never by its path or filename |
| parse | `pipeline/parse.py` | typed records per document type, every field extracted independently; all money through one `parse_inr` on `Decimal` |
| store | `pipeline/store.py` | joins them into `build/facts.db` — works, invoices, credentials, CVs, bonds, dossiers — and cross-checks the client's certificate against the contractor's own |
| resolve | `pipeline/anchor.py`, `pipeline/facts.py` | which client, engineer, work and certificate a question points at; fuzzy on the question side, exact on the corpus side |
| plan | `pipeline/planner.py` (model), `pipeline/rules.py` (grammar) | question → plan |
| execute | `pipeline/dsl.py` | plan → number, in exact arithmetic |
| reconcile | `pipeline/solve.py` | one answer per question, and the record of how it was reached |

Progress goes to stdout at every stage, and `build/run_log.json` records, for each question, the
plan chosen, the entities resolved, the derivation, the disagreements and the errors.

---

## Ingestion does not trust the filesystem

The estate is nested by document type and the nesting will not match any sample. So no part of this
pipeline reads a path to decide what a document is. Each file is classified by title-line
signatures against its own text, with several independent cues per type, and the two
completion-certificate families are separated by **who issued them**: the client's sign-off carries
the client's letterhead, the contractor's own record carries the contractor's. Which letterhead is
the contractor's is not hard-coded either — it is whichever one appears on the most documents in
the estate.

Measured against the shipped corpus's own index, content classification is **678/678 PDFs**, and
every type count matches the briefing exactly (155 completion certificates, 155 company
certificates, 132 reference letters, 60 bonds, 48 personnel certificates, 39 CVs, 9 workbooks).
Workbooks are identified by their column headers, not their filenames, so an ageing book named
anything at all still parses as long as it has a client column and an invoiced column.

---

## Who wins when the planners disagree

This was the hardest call in the build, and it is settled by measurement rather than preference.

Sampling the model three times and taking the majority protects against a model that misreads at
random. It does not protect against a model that misreads *the same question the same way every
time*, which is how a real model actually fails. Against a mock endpoint that returns a plausible
misreading for 30% of questions:

| model behaviour | `--trust rules` | `--trust consensus` (default) | `--trust model` |
|---|---|---|---|
| faithful | 0.994 | **0.994** | 0.994 |
| 30% wrong, independently per sample | 0.994 | **0.994** | 0.868 |
| 30% wrong, same misreading every sample | 0.994 | **0.949** | 0.811 |

So majority voting alone cannot justify overturning the rules, which are measurably right on 331
of the 333 questions we can check. The default policy instead gives the model **only the questions
the rules admit they are unsure about** — no cue fired, the plan would not run, or the client had
to be inferred from an engineer's portfolio rather than read from the question — and requires its
plans to be unanimous even there. That is 20% of the visible set. Everywhere else the rules stand
and the model's dissent is recorded in the log instead of acted on.

`--trust rules` is the kill switch if the endpoint turns out to be unreliable on the day;
`--trust model` is the opposite bet.

---

## If the endpoint is unavailable

The run completes anyway, with the rule-based planner answering everything. The endpoint is probed
once before any batch is dispatched; a dead endpoint costs about two seconds and prints a line
saying so. Concurrency is bounded (default 8 in flight) because the server is shared, every failure
backs off rather than retrying immediately, and `--llm-budget` caps the wall clock the planning
stage may spend before the remaining questions fall through to the rules.

The client also handles the three documented traps: it asks for generous `max_tokens` and treats a
`finish_reason: "length"` with null content as retryable rather than as an empty answer; it never
reads the reasoning trace; and if the server rejects `response_format`, it re-asks for JSON in the
prompt and validates the reply itself.

`dev/mock_llm.py` is a stand-in endpoint that reproduces each of those failure modes on demand,
which is how they were tested.

---

## What is measured, and on what

`dev/` holds development-only tooling. **Nothing under `pipeline/` imports any of it**, and the
graded run has no answers to look at.

- `dev/gold_visible.json` — the 333 answers from a previously scored run of the visible question
  set, kept as a regression benchmark. It is the only ground truth available, and it is used to
  measure the pipeline, never to answer with. There is no answer recall path in this repository:
  the previous version's `qa/overrides.py` and its answer bank have been deleted.
- `dev/score.py` — scores a submission under this round's tolerances.
- `dev/check_classifier.py` — scores document classification against the corpus index.
- `dev/mock_llm.py` — the fake endpoint described above.

Current standing on the visible set, with the model disabled entirely:

```
exact-match: 331/333 = 0.9940
   count     10/ 10       days      24/ 24
   money    266/268       percent   31/ 31
```

The two misses are the same question: one that names an engineer and a credential but no client,
and asks about "that client's works". The engineer has six clients and one work with each; nothing
in the corpus says which one is meant. `primary_client_of` guesses, and on those two it guesses
wrong. It is roughly 0.6% of a set this size and I have not found a principled rule that recovers
it — the ordering, value and recency signals all point different ways on the two cases.

---

## Notes on the design

**The fact store is the deliverable, not the retriever.** There is no embedding index and no
reranker in this pipeline. Questions here are not lexical-similarity problems — they are joins
("find the engineer's certificate, find the work, find its client, gather *every* work of that
client, total the values from their own certificates"). A join is what a fact store is for, and it
is exact where a retriever is approximate. Ingestion of 687 documents takes about 25 seconds and
the whole run, model aside, is under a minute.

**Entity resolution is where the score is won or lost, not arithmetic.** Correct arithmetic over
the wrong client passes every range check and every sanity audit there is. Client matching is
IDF-weighted so unique tokens ("trishakti") decide and generic ones ("corporation") are near noise;
words that also appear in *work* names are heavily discounted, because "Steel Truss Bridge" once
selected Mahanadi **Steel** Corporation; a state is only ever a tiebreak; and when a question names
a client without its state, the categories it goes on to mention break the tie.

**The rule-based planner is not a phrase list.** The previous version's classifier was fitted to
the phrasings of the released question set — 102 alternatives, 71 of them carrying two questions or
fewer — and scored 0.232 when each question's matched phrase was removed. This one is written from
what each shape *means*: stems rather than whole phrases, and structural cues that do not depend on
wording at all — two years named, a parseable rupee amount, two category labels, a superlative with
a runner-up.

**Corpus-scope questions.** The exhaustive tier asks about the whole estate rather than one client,
so both planners can set a measure's scope to `corpus`. The rules require this to be said
explicitly ("all clients", "company-wide"); "portfolio" is deliberately not a cue, because a
portfolio in this corpus is nearly always one client's or one engineer's.

**Nothing is left blank.** An unanswered row scores zero and a wrong one costs nothing, so a
question whose plans all fail still gets a scale-appropriate figure for its answer type.

## Known limitations

- Questions that name an engineer but no client are guesses when the engineer has no dominant
  client. Two of 333 on the visible set.
- Grading filters depend on a written grading that only one certificate family states; the other
  family has none, so a grading question over a client whose works are all prose-family will find
  nothing and fall through.
- Bonds and tender dossiers are parsed and stored but no question shape targets them yet; they are
  there because an exhaustive-tier question might.
- The model path is tested for plumbing, degradation and reconciliation against a mock endpoint.
  Its *judgement* has never been tested, because no endpoint was reachable during development. The
  reconciliation policy is built on that assumption rather than around it.
