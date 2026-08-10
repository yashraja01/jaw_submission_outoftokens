# METHODOLOGY — JAW 2026 / GikaGraph

Document-grounded numerical QA over the National Infrastructure Corp. Ltd. archive.

**Status:** Phase 1 complete. `submission.csv` — 371/371 answered, 0 fallbacks, samples 23/23,
all three README golds exact. Build detail and judgement calls: **[`logging.md`](logging.md)**.
**Last updated:** 2026-08-10.

Two Phase-0 figures were corrected during the build:
- distinct client **names is 28, not 29** (the recon counted a null as a value);
- there is a **21st shape, `mean_minus_median`** (19 questions), which Phase 0 folded into
  `avg_work_size`. That closes the gap to the README's stated 21 patterns.

---

## 0. Executive summary

Recon confirms the brief's central claim — the answer is almost never in the document the question
names — while falsifying several of its premises. Since the first draft, the organisers shipped a
release that changes the task materially (§1). The current position:

1. **The corpus is far more structured than "unstructured documents" implies.** Every completed work
   carries a synthetic key (`Pkg-N`), and its completion certificate carries
   `CC/{client_id}/{year}/{pkg}`. Entity resolution is key-joining, not fuzzy matching.
2. **The scored set is 371 known questions, not a hidden set**, and scoring is now continuous.
3. **Three gold answers are visible in the README's format example, and all three reproduce exactly
   from the recon fact table** (§4.2). Together with 21/23 samples, that is 24 gold answers matched.
4. **~155 documents are needed by no question at all** (§3.3). The effort that was budgeted for
   ledgers, bonds and financial statements is worth approximately zero and has been reallocated.

---

## 1. What changed in release `hidden_set_v1.0`

Pulled commit `b043279`. Three changes, each consequential:

| | Before | Now |
|---|---|---|
| Questions | 23 samples + hidden set | **`questions.json`, 371 questions, given to us** |
| Submission | JSONL `{"qid","answer"}` | **CSV `question_id,answer`** |
| Scoring | banded (0.5 / 2 / 10%) → 1.0 / 0.7 / 0.3 | **`max(0, 1 − |err|/|gold|)`, continuous** |

### 1.1 The scoring change rewrites the strategy

`score = max(0, 1 − |got − gold| / |gold|)`, averaged over 371. There are no bands, no cliffs, and
no floor above zero. Three consequences, in order of importance:

- **Magnitude errors are now the only catastrophic failure.** Being 100% out scores 0, and so does
  being 500% out — but being 30% out scores 0.70. Under the old bands, 30% out scored 0. The
  penalty curve has flattened everywhere except at the extremes.
- **Therefore shape misclassification is now the dominant risk, not extraction precision.** If the
  planner reads a `hop_aggregate` as an `avg_work_size` it returns total/n; for a 9-work client
  that is an 89% error scoring 0.11. Extraction that is 0.5% off scores 0.995. **The question
  parser is worth an order of magnitude more than the last decimal of extraction.** My earlier
  draft had this backwards.
- **Never emit 0 or blank.** Both score exactly zero, and a shape-appropriate median typically
  scores 0.4–0.7. The fallback ladder is now a scoring feature, not a safety net.

Small-gold counts are the harsh case: gold 1, answered 2 → **0.0**; gold 2, answered 3 → 0.5. The
`absence` and `distinct_category` shapes have little tolerance. Percentages are now forgiving
(90.19 vs 90 → 0.998), reversing the old regime where they were graded as counts.

My earlier claim that "percent and count questions carry no partial-credit cushion" was true of the
old scorer and is now wrong; the correction is above.

---

## 2. Corpus recon

687 documents reconcile against `document_index.csv`. PyMuPDF with `sort=True` recovers
**3,508,164 characters**, matching the briefing's "roughly 3.5 million" — evidence that nothing is
silently truncated.

### 2.1 The spine: 155 works

| Fact | Value | How established |
|---|---|---|
| Works | 155 | `DOC-CC-001` … `-155`, one per work |
| `DOC-CC-NNN` ↔ `Pkg-NNN` | **0 mismatches / 155** | verified |
| Σ contract values | **₹55,303,999,999 = 5,530.40 Cr** | README "~₹5,530 crore" ✅ |
| Categories | 13 canonical labels | certificate and portfolio agree **155/155** |
| Reference letters | 132, **1:1**, none doubled | 132/132 resolved; **23 works unreferenced** |
| Roles | Prime 96 / JV Partner 59 | portfolio; 0 disagreements against 44 reference letters |

Three unrelated derivations agree (value total, character count, category cross-check), which is
stronger evidence for the extraction layer than the sample replay is.

### 2.2 Client identity — resolved, and corroborated

The certificate ref's middle field is a client *record* id running **1..62** (60 have completed
works; ids 6 and 53 have none) — which is exactly the README's "62 client organisations". But there
are only **29 distinct client names**: the generator drew 62 records cyclically from 29 names.

**Gold answers group by name, not by record id.** Three discriminating cases, all confirming, none
disconfirming:

| Question | Client | Record ids | Name-grouped total | Gold |
|---|---|---|---|---|
| `HS-IC-0008` | Lakshya Engineering & Construction | 15, 32, 49 | 7 works, 1,944,300,000 | ✅ |
| `HV-IC-0001` | National Expressway Development Authority | 3, 20, 37, 54 | 9 works, 2,942,400,000 | ✅ |
| `HV-IC-0002` | Irrigation & Waterways Dept, Govt of Rajasthan | 5, 39 | 6 works, 1,957,800,000 | ✅ |

I searched specifically for the converse — a question where name-grouping over-collects and gold
matches the narrower id set. **There is none among the 26 gold answers available.** This is now
frozen, behind a single switch.

### 2.3 Layout families

Completion certificates split into exactly two: **table (84)** and **prose (71)**. Five prose
certificates are justified to intra-word spacing, which masquerades as a third family until
whitespace is collapsed. **Normalise whitespace before every regex.**

Grading appears in **all 84 table certificates and none of the 71 prose ones** — which is precisely
why the organisers withdrew the grading questions. Three survive in the scored set (§3.4).

Four date renderings coexist within a single family: `2011-02-06`, `11/01/2013`, `17 Nov 2010`,
`October 8, 2024`. ISO-only parsing loses 53 of 155.

Money: **65 crore-denominated, 53 lakh, 37 plain**. Lossless to within ₹1 (`19.33 Cr` for a true
193,299,999) — 5×10⁻⁹ relative error, immaterial under any scorer.

### 2.4 Other sources

- **Portfolio (`DOC-PPP-001`)** — all 155 works with client, **role**, category, value, date, cert
  ref. Its values are rounded to 2dp crore, so its total is **12 Cr short** of the certificates.
  Use for role and cross-validation; never for money.
- **`Receivables_Ageing.xlsx`** — 518 invoices with a client column, invoiced / received /
  outstanding. Joins cleanly to the 29 client names. **This one workbook is worth 15.6% of the
  score** (§3.2).
- **CVs (39)** — carry `Business Unit`, needed by 6.5% of questions.
- **Financial statements** — see §5.2. Needed by no question.

---

## 3. The question set

371 questions, `answer_type` only — no `shape` field, so shapes are mine. Types: **money 233,
count 59, percent 55, days 24**.

### 3.1 Shape distribution (measured, not guessed)

| Shape | n | Share | Out | Needs |
|---|--:|--:|---|---|
| `avg_work_size` | 41 | 11.1% | money | works |
| `hop_aggregate` | 35 | 9.4% | money | works |
| `awarded_vs_invoiced` | 32 | 8.6% | money | **ageing** |
| `collection_pct` | 26 | 7.0% | percent | **ageing** |
| `business_units` | 24 | 6.5% | count | **CVs** |
| `date_span` | 24 | 6.5% | days | works + credentials |
| `pair_overlap` | 24 | 6.5% | count | works + credentials |
| `largest_client_share` | 23 | 6.2% | percent | works |
| `year_delta` | 22 | 5.9% | money | works |
| `role_split` | 18 | 4.9% | money | works |
| `top_n_clients` | 18 | 4.9% | money | works |
| `exclusion_aggregate` | 17 | 4.6% | money | works |
| `rank_value` | 17 | 4.6% | money | works |
| `threshold_aggregate` | 16 | 4.3% | money | works |
| `temporal_chain` | 12 | 3.2% | money | works + credentials |
| `distinct_category` | 9 | 2.4% | count | works |
| `referenced_share` | 6 | 1.6% | percent | works + letters |
| `grading_filter` | 3 | 0.8% | money | **grading (84/155)** |
| `gap_to_threshold` | 2 | 0.5% | money | works |
| `absence` | 2 | 0.5% | count | works + letters |

20 shapes against the README's stated 21 — the residual is probably `role_split` splitting on
Prime vs JV Partner. Classification is regex-based and approximate at the margin; Phase 4 replaces
it with a constrained LLM parse. It is accurate enough to allocate against.

**Eight shapes are new relative to the samples**: `awarded_vs_invoiced`, `collection_pct`,
`business_units`, `pair_overlap`, `largest_client_share`, `year_delta`, `top_n_clients`,
`grading_filter`. Together **170 questions, 45.8% of the score.** My predicted list was directionally
right on receivables and wrong on everything else.

### 3.2 Allocation by data source — the decisive cut

| Source | Questions | Share | Status |
|---|--:|--:|---|
| Work table (certificates, letters, credentials, portfolio) | 286 | **77.1%** | recon parser already reproduces gold |
| `Receivables_Ageing.xlsx` | 58 | **15.6%** | joins cleanly; not yet built |
| CV `Business Unit` field | 24 | **6.5%** | not yet built |
| Certificate grading | 3 | 0.8% | 84/155 coverage |

### 3.3 What no question needs

Zero of 371 questions mention bonds or guarantees, ISO or accreditation, turnover / revenue /
profit / balance sheet, plant or assets, BOQ or rates or RA bills, tender dossiers, compliance
matrices, or headcount. (Five apparent "ledger" hits are rhetorical — *"cross-checking against the
master ledger"* on a question about client share.)

**That is roughly 155 documents — performance bonds (60), compliance matrices (40), RA bills (12),
bank statements (8), general ledgers (8), financial statements (7), tender dossiers (6), ISO
certificates (5), annual reports (2), and 8 of the 9 workbooks — required by nothing.** They will be
extracted and cached because it is cheap, but no parser, schema or query path will be built for
them.

### 3.4 The grading questions

All three (`HV-IC-0058`, `0066`, `0137`) ask the same thing: the total value of Arunodaya
Infrastructure works the client marked *Satisfactory*. The organisers withdrew this shape from the
samples because grading is absent from the 71 prose certificates — yet it is in the scored set.
Under continuous scoring these are worth 0.8%, and a grading-filtered subtotal computed from
whatever certificates *do* state a grading will land far closer than a zero. Compute and submit.

### 3.5 Arithmetic conventions (confirmed against gold)

| Type | Rule | Evidence |
|---|---|---|
| money | round to nearest integer | `HS-IC-0012`: 688,499,999/3 → gold 229,500,000 |
| percent | `round(100·a/b, 2)` out of 100 | 33.33, 66.67, **90.19** |
| days | `(end − start).days` | `HS-IC-0003` 1569, `HS-IC-0004` 646 |
| count | plain integer | |

### 3.6 The anchor-is-a-pointer rule

Questions routinely open with a credential and a named project — *"Starting with Rajesh Rao's Six
Sigma Black Belt on Material Handling Plant — Pkg-47 …"*. In `hop_aggregate`, `avg_work_size`,
`collection_pct`, `top_n_clients` and `largest_client_share`, that anchor is **navigation only: a
route to the client, then discarded.** The aggregation runs over the client's entire portfolio.

Verified on gold three ways: `HV-IC-0001` (Rajesh Rao → NEDA → all 9 works), `HS-IC-0007` (Menon
leads 8, client has 6, gold = the client's 6), `HS-IC-0008` (Chopra leads 9, client has 7, gold = 7).

The converse holds for `distinct_category`, `temporal_chain`, `date_span`, `pair_overlap` and
`top_n_clients`, where the engineer genuinely scopes the set. **This is a planner flag per shape,
not something inferable from phrasing**, and it is the trap the organisers said they built questions
to punish.

---

## 4. Architecture

```
documents/ ──▶ extract/ ──▶ facts/ ──▶ graph/ ──▶ qa/ ──▶ submission.csv
               text+tables  typed     SQLite +   plan +
               cached       records   assertions execute
```

**Extraction.** PyMuPDF `sort=True` for prose and coverage; pdfplumber `extract_tables()` for table
structure. Benchmarked on `DOC-FS-2025` p1: PyMuPDF recovered **1,879 chars**, pdfplumber's default
text **880** — it silently dropped half the page, the exact failure the briefing warns about. But
pdfplumber's *table* extraction returns clean typed rows PyMuPDF cannot. Use both, for different
things. openpyxl twice per workbook (`data_only` False/True) for formulas and cached values.

**Facts.** Typed records, provenance on every field (`doc_id`, page, source string, parsed value).
Deterministic parsers per layout family. **No LLM extraction pass is planned** — deterministic
coverage is 155/155 on every field the scored questions need, and the one prose-only field
(grading) is 0.8% of the score with a known structural gap.

**Graph.** SQLite plus an in-memory graph — inspectable by hand, which is how the portfolio rounding
error was caught.

**QA.** LLM parses each question into `{shape, anchor_entities, scope_flag, filters, aggregation,
output_type}`, constrained to the 20 shapes. Anchor mentions resolve against the entity tables —
never accepted as free text, and questions are heavily corrupted on purpose (*"irr & waterways dept
rajasthan"*, *"ut pr pkg 2 wtp augmentation"*, *"mega infra authority"*), so resolution must be
fuzzy on the question side and exact on the corpus side.

**Execution.** Pure Python over SQLite. Full derivation logged per answer.

### 4.1 Fallback ladder

Full plan → partial aggregate → **shape-conditional median** → never blank, never 0. Under
continuous scoring a shape-appropriate median is worth an estimated 0.4–0.7 where a blank is 0.

### 4.2 Validation already achieved

Twenty-four gold answers reproduce from the recon table: 21 of 23 samples (the 2 misses were one
line-wrap bug, since fixed), plus all three README format examples — which are real answers, on the
*scored* set, on three different shapes:

| qid | Shape | Gold | Recon |
|---|---|---|---|
| `HV-IC-0001` | `hop_aggregate` | 2942400000 | ✅ exact |
| `HV-IC-0002` | `exclusion_aggregate` | 1516600000 | ✅ exact |
| `HV-IC-0003` | `collection_pct` | 90.19 | ✅ exact (313,643,044 / 347,767,752) |

These are the perturbation test: new anchors, new clients, unseen questions, three distinct shapes.

---

## 5. Resolved questions

### 5.1 Category exclusion — **exact-label** (was §6.3)

Settled empirically, as directed. The 19 exclusion questions name canonical labels directly, and
the generator writes **both** members of every apparent family:

- `HV-IC-0216` "excluding **buildings**" and `HV-IC-0328` "excluding **small buildings**"
- `HS-IC-0016` "excluding **roads maintenance**" and `HV-IC-0292` "excluding **roads highways**"

If a bare head meant the family, "small buildings" would never need writing. It is written.
Confirmed against gold: `HV-IC-0002` excludes only `water treatment` while retaining `irrigation`
(2 works, 505.9M) and `large bridges` — exact-label reproduces 1,516,600,000 exactly; a "water"
family filter would not. Implemented as a flag, defaulting to exact.

### 5.2 Financial statements — column shift confirmed, and moot

You were right that word-position evidence proves location, not column identity. Pulling x-positions
across all rows: two-column rows place current year at x≈353–392 and prior year at x≈506–523. The
profit block has **one** number, at **x≈503–512 — the prior-year column**. So there is a genuine
one-column shift, exactly the signature you predicted.

But it is not the prior-year figure either: prior-year A−B = 2,442 against a stated −13,488 (and in
FS-2019, 444 against −2,803). No column assignment makes the P&L consistent. Both statements are
internally coherent only downward — PBT − Tax = PAT, at an identical 23.08% rate in both eras — so
the generator produced a PBT unrelated to its own revenue and expense lines.

**I do not have a column-mapping bug:** the two-column rows read current-then-prior correctly, and
pdfplumber's table extraction independently agrees. The rule stands — read stated line items, never
derive — and it is moot regardless, because no question touches financial statements. No divergence
alarm will be built there, per your instruction.

---

## 6. Remaining risks

1. **Shape classification accuracy** is now the top risk (§1.1). Mitigation: constrained LLM parse,
   plus a magnitude sanity check per shape before emission.
2. **Zero-denominator collection questions.** Four clients have completed works but **no invoices**
   (I&W West Bengal, Jal Nigam UP, Jharkhand Municipal Corp, PWD Maharashtra); one has invoices and
   no works (PHED West Bengal). `collection_pct` is 0/0 for those. Needs an explicit rule, not a
   crash — and since these are percentages, a sane default is cheap.
3. **`pair_overlap` union vs intersection.** Some questions say "both" (intersection), others
   "combined tally" (ambiguous). 24 questions ride on reading this right. No gold available; will
   resolve by checking which reading yields plausible small integers across all 24.
4. **Threshold inclusivity** (`≥` vs `>`) remains untested — no work sits exactly on a bar.
5. **`business_units`** requires a CV → engineer → works → client chain not yet built or validated.

---

## 7. Plan

| Phase | Work | Est. |
|---|---|---|
| 1 | Extraction layer, both extractors, coverage gate | 3 h |
| 2 | Typed facts + provenance for the **286-question core** | 5 h |
| 3 | Entity resolution, SQLite, integrity assertions | 3 h |
| 4 | **Ageing workbook (15.6%) + CV business units (6.5%)** | 5 h |
| 5 | Question parser → plan; anchor resolution; scope flag | 8 h |
| 6 | Executor, derivation log, fallback ladder | 4 h |
| 7 | Eval, magnitude sanity checks, hardening | 5 h |

Phase 5 is now the largest line. Under continuous scoring the parser, not the extractor, is where
the marginal point lives.

**Standing rule:** a complete, submittable `submission.csv` with all 371 rows exists from the end of
Phase 6 onward, and is regenerated after every change.

---

## 8. Deliverables

```
extract/   text + table + workbook extraction, cached
facts/     schemas, per-document extraction, provenance
graph/     entity resolution, SQLite build, integrity assertions
qa/        question parser, query planner, executor
eval/      scoring harness, error classification
run.py     questions.json → submission.csv
METHODOLOGY.md
```
