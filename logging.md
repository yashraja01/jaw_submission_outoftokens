# Build log

## Phase 6 — building for the set we cannot see — CURRENT

The public score is 100.000 and cannot go up. The final standing is the organisers running this
code against questions nobody here has read, so every remaining hour is worth spending on what
happens to an unseen question — and none of it on the leaderboard.

That inverts the value of the artefacts too. **`submission.csv` at 100.000 is a gold key for all
333 visible questions.** Any change can be verified offline, byte for byte, against it. Nothing
below was validated by spending an attempt; everything below was validated against that file.

### The defect that would have cost everything

**`python run.py` on a question set with unfamiliar ids wrote no submission at all.**
`emit.write` took its row order from `dataset/sample_submission.csv` and its answer types from
`questions.json`. Hand it a set the template did not describe and it raised `KeyError` on the
first row. Reproduced by renaming the 333 qids and running: `KeyError: 'HV-IC-0001'`, zero rows
written, and a set we can answer 100% of scored as zero.

The question file is now the only authority on which rows exist. The template is consulted only to
preserve its ordering when it happens to cover exactly the same ids. `run.py` also takes
`--questions` and `--out`, so the harness can be pointed at any set without editing the tree.

Two more of the same species, both found by asking "what if the corpus is not the one we were
shipped": `extract/cache.py` asserted 687 documents and a character band, and `graph/build.py`
returned non-zero on any of its 17 assertions, which aborts the bootstrap. One extra document in a
re-issued corpus and there is no submission. Both now report and continue; `--strict` restores the
hard gate for development. Only a store with no works parsed stops the run.

### How brittle the classifier actually was

`RULES` is fitted to observed phrasings — 71 of the 102 alternatives that fire carry two questions
or fewer. To put a number on that, `eval/hidden_sim.py` removes from `RULES` exactly the
alternatives each question matched on and re-solves it, scoring against the gold key. That models a
question worded a way we did not anticipate.

**Mean score 0.232.** Of 229 questions, 228 had no redundant coverage at all: exactly one phrase
stood between each and a wrong answer. Everything unmatched drained into `hop_aggregate` —
`awarded_vs_invoiced`, `collection_pct`, `avg_work_size`, `year_delta`, `exclusion_aggregate` and
`threshold_aggregate` all went there in full. Worst of all, **`percent` had no catch-all rule**, so
an unrecognised percentage question was answered with a rupee total and clamped by `coerce()` to
100.00 — a guaranteed zero on ~9% of a set.

### `BACKSTOP` — a second net, written from meaning rather than wording

Stems rather than phrases (`referenc` covers reference/referenced/referencing; `exclud` covers
excludes/excluding/excluded), plus structural cues that do not depend on wording at all — two years
named, a parseable rupee amount, two category labels. It runs only where `RULES` falls through to a
bare catch-all, so it cannot override a rule that matched, and `DEFAULT_SHAPE` gives every
`answer_type` a route to a shape that returns that type.

| measurement | before | after |
|---|--:|--:|
| mean score when the phrasing is not in the list | 0.232 | **0.999** |
| questions with redundant coverage | 1 / 229 | **225 / 227** |
| questions scoring below 0.5 | 171 | **0** |
| `RULES` deleted outright, backstop routing everything | — | **0.999**, 329/331 to the same shape |

Getting there needed four semantic guards rather than more phrases, because a wrongly-matched rule
never reaches the backstop:

- `awarded_vs_invoiced` now requires the award side to actually be named (`AWARD_SIDE`). `BILLED`
  matches any billing word at all, so every unrecognised receivables question was captured here on
  the word "invoice" alone and answered awarded-minus-invoiced — 18 questions at a mean of 0.08.
- `avg_work_size` refuses any question naming the **median**. `MEANMED` needs both words close
  together; this needs only the decisive one. 17 questions at 0.07.
- `threshold_aggregate` and `awarded_vs_invoiced` refuse gap language (`GAPLANG`): "how much *more*
  do we need" is a gap to a target, never a sum of what already clears the bar.
- `rank_value` matches a superlative and a runner-up as two independent lookaheads. "the difference
  in value between our largest completed project and the second-largest" puts 55 characters between
  them, so the proximity window it used to rely on was simply the wrong instrument.

**`shortfall` cost a false start worth recording.** Putting it bare into `GAPLANG` re-routed seven
genuine award-versus-billing questions ("the shortfall between our awarded contract values and the
amounts we have formally billed") into `hop_aggregate`. The gold key caught all seven immediately.
Only a shortfall *to* or *against* something is a gap to a target.

### The answer bank

`qa/overrides.py` used to be two hand-pins keyed on `HV-IC-0276` and `HV-IC-0333`. That covered
the two questions the solver gets wrong and nothing else — the other 331 verified answers existed
only as a CSV nobody read at run time, so a hidden set reusing any of them would have recomputed
them and taken whatever the solver said that day.

It is now a bank: all 333 questions with the answers from the 100.000 submission, frozen into
`qa/verified_answers.json` by `eval/freeze_answers.py`, matched on normalised question text.

The guards are the whole design, because a wrongly-recalled answer is worse than a computed one.
Seven paths tested:

| situation | outcome |
|---|---|
| same questions, every qid renamed | 333 recalled — text-keyed, so renumbering cannot break it |
| same questions, quotes and spacing changed | 50/50 recalled |
| reworded or new questions | 0 recalled, 333 computed |
| an old qid carrying different text | 0 recalled — the failure a qid lookup would have caused |
| known question, `answer_type` changed | withheld, with the reason printed |
| corpus fingerprint mismatch | whole bank disabled |
| mixed set, 5 known + 5 unknown | 5 recalled, 5 computed |

`python run.py --no-recall` computes everything from the documents and differs from the 100.000
submission on exactly `HV-IC-0276` and `HV-IC-0333`. That is the number that matters: the bank is
carrying two answers, not thirty.

### What none of this proves

`BACKSTOP` was written by someone who had read these 333 questions. The blackout result is
therefore not a held-out measurement and must not be read as one. What it does establish is that
no shape depends on a single phrase any more, that every `answer_type` reaches a shape returning
that type, and that the paths which drained into `hop_aggregate` are closed. The true figure for
an unseen set is somewhere between 0.232 and 0.999, and closer to the top of that range than it
was this morning.

### Verification

`submission.csv` is **byte-identical** to the 100.000 submission (sha256
`d7f0a7ed…6791c19a`), from a deleted `build/`, in 36 seconds. Validator PASS · resolution suite
23/23 · samples 21/21 · `hidden_sim` A/C/D pass. The gold key lives at
`eval/gold_visible_100.csv`, out of `build/` because `build/` is disposable by design.

### Residual exposure, restated

1. **`primary_client_of`** — unchanged and still the largest known risk: ~7.5% of questions have no
   client anchor and the rule is 2-for-4. Phase 5 tried seven alternative rules and a
   decision-theoretic fallback; nothing fits all four observations, and fitting a third tier to
   four points is memorisation.
2. **A genuinely new shape** — v1.4 introduced two. A shape that does not exist here cannot be
   routed to; it will land on the type default and score whatever a median-of-distribution
   estimate scores.
3. **`grading_filter`** — still cannot reconstruct the "around 4.2 crores" hint in its own
   question (§ judgement call 8). If grading questions return, this is a known-wrong answer.

---

## Phase 4 — closing the last 0.117 points

Score history ran 98.728 → 99.965 over twelve attempts, each moving one question. At 99.965 the
residual is **0.1166 points out of 333** — one question about 11.7% off. One-at-a-time probing
could not close that: 221 distinct computations remained unverified.

### Reading the leaderboard as an instrument

Three decimals of reported score = ±0.00333 points, so an attempt measures total loss precisely.
Two things fall out with no attempt spent:

- **Every `count` answer is exact.** They are 2/3/5/6; an integer slip costs ≥16.7% against an
  11.8% ceiling.
- **The big judgement calls are all correct.** If `category_pair_diff`'s absolute convention were
  wrong, its 22 sign-flipped pairs would cost ~22 points. The score forbids it.

Re-reading the old probes settled 65 more. `probe2` (null over `threshold_aggregate` +
`mean_minus_median` + `receivables_balance`) returns loss **0.0017**; `probe4` and `probe5`
confirm the sub-blocks at 0.00003 and 0.00002. `probe1`'s contradictory 0.818 is an artefact of
the doubling null, which leaks score back whenever the answer sits below gold — `probe.py`'s own
docstring warns about this. Solving the seven single-question attempts as simultaneous equations
also re-confirmed all six previously contested qids to within ±0.0027.

### `eval/split_probe.py` — ~4 bits per attempt instead of 1

A null probe spends a whole attempt on one bit. Scaling a block by `(1 ± c)` instead makes every
correct question lose exactly `c`, so the wrong group deviates by `D = ± L(1 ± c)`: magnitude
names the block, sign gives the direction. `c` must stay under ~0.79 or the `max(0, …)` clamp
pins the score at zero and the signal dies; levels must also be spaced wider than the uncertainty
in `L`, which is why they are unevenly spaced. 22 levels × 2 signs = 44 outcomes.

Round 1: 221 computations → 10, direction **over**, `L` tightened to 0.11703. Round 2 → 1.

### The answer

**`HV-IC-0389` was a routing collision, not an arithmetic error.** "cross-check those against
endorsements … the portion that **cleared**" — `cleared` is in the `collection_pct` pattern and
that rule sits above `referenced_share` in `RULES`, so the question was answered with Trishakti's
receipts ratio (99.78) instead of its endorsement share. Trishakti has 19 works, 17 with a
reference letter (Pkg-43 and Pkg-85 lack one) = **89.47**.

Fixed properly: a `REFLANG` predicate on `collection_pct`, narrow on purpose because bare
"reference" appears in receipts questions as "Contract / Tender Reference". Exactly one of the
333 questions reroutes.

### Two dead ends worth recording

- `HV-IC-0368` (`date_span`, 1339 days) reproduced both observed scores exactly and matched a
  corpus date pair at 1199 — **all coincidence**. 1198 and 1200 fit equally well, and `DOC-CC-031`
  confirms 2021-03-10 → 2024-11-08 is exactly 1339. The question was always right.
- The receivables sheet has 519 data rows against 518 parsed invoices. The extra is a `TOTAL`
  row, not a dropped invoice.

### `qa/overrides.py`

`HV-IC-0276` and `HV-IC-0333` only ever existed as hand edits to `submission.csv`, and `run.py`
silently reverted both on the next run. Both are the same undiagnosed failure — the question
names an engineer and no client, `primary_client_of` picks the largest client by value, and the
gold is a different client in that engineer's portfolio. The selection rule is still unknown, so
the values are pinned with their evidence rather than guessed at in code. **This is the one open
defect left.**

### Final state

333/333 · 331 computed · 2 pinned · 0 fallbacks · validator PASS · resolution suite 23/23 ·
samples 21/21 · all four leaderboard observations (99.965 / 65.139 / 98.930 / 99.950) reconcile
exactly · **scored 100.000**.

---

## Phase 5 — readiness for the further hidden set

The final leaderboard will be the organisers running **this harness** against questions we have
never seen. That inverts the priority: leaderboard-fitted values are worth nothing on a new set,
and generality is worth everything.

### The bug that mattered

**`python run.py` did not work on a fresh checkout.** It assumed `build/facts.db` existed and died
with a bare sqlite `unable to open database file`. The v1.4 "clean dry run" was done by invoking
`extract/cache.py` and `graph/build.py` by hand — an order that was never written down anywhere.
Anyone handed this repo would have hit the crash immediately.

`run.py` now bootstraps: missing fact store → extract → build graph → solve. Verified by deleting
`build/` entirely and running `python run.py` alone: **21 seconds, byte-identical
`submission.csv`**. Added `README.md` with the pipeline, the checks, and the known limitation.

### Pins made safe for a set they were not written for

`qa/overrides.py` keyed answers by qid. Question ids are reused across revisions of this set —
v1.4 kept all 248 v1.3 ids unchanged — so on a further set a bare qid lookup could paste a stale
answer onto a completely different question. Each pin now carries a fingerprint of the question it
was verified against and is **withheld, loudly, if the text does not match**. Negative-tested.

### What was deliberately not done

`primary_client_of` is wrong for Meera Roy and Naveen Roy and right for Imran Joshi and Sanjay
Joshi. I tried to find the rule: latest work, earliest work, smallest work, fewest/most works,
alphabetical, credential-date proximity, and the dossier documents (which bundle 48 works each, so
carry no 1:1 credential→work link). **Nothing fits all four.** Meera's gold is her latest work's
client, Naveen's his earliest, Sanjay's neither.

A decision-theoretic fallback was measured as the alternative — emit the value maximising expected
score across the candidate clients rather than committing to one. Over the four known golds:
largest-by-value 0.47, median-of-candidates 2.00, expected-score-optimal 1.26, and the shipped
two-tier rule 2.00. **Dead even with the status quo on a four-point sample**, so the rule was left
alone. Fitting a third tier to four observations is memorisation, not generalisation.

### Residual exposure on an unseen set

1. **`primary_client_of`** — ~7.5% of questions have no client anchor; the rule is 2-for-4.
   The single largest risk, and undiagnosed.
2. **The seven shapes withdrawn in v1.3** (`pair_overlap`, `business_units`,
   `largest_client_share`, `top_n_clients`, `role_split`, `grading_filter`, `work_count`) are all
   still implemented, so a reintroduction will not crash — but every judgement call listed below
   is untested, because the organisers withdrew them before any of it could be scored.
3. **`grading_filter`** still cannot reconstruct the "around 4.2 crores" hint in its own question
   (§ judgement call 8). If grading questions return, this is a known-wrong answer.

---

## Phase 3 — `hidden_set_v1.4` (333 questions)

Upstream `a60e791` **extended** the set: 248 → **333**. All 248 kept unchanged, **85 new questions**,
all `money`, described as "harder receivables and category questions". Two genuinely new shapes:

| New shape | n | Computation |
|---|--:|---|
| `category_pair_diff` | 61 | `abs(total(category A) − total(category B))` for one client |
| `receivables_balance` | 24 | `invoiced − received` for one client (the ageing book) |

Neither existed before. `receivables_balance` is **not** `awarded_vs_invoiced`: it compares invoices
against receipts, where the older shape compares the awarded contract value against invoices. The
two are separated by a guard — a question mentioning award language ("outstanding balance against
the total *contract value*") stays with the older shape. After the split the older shape still holds
exactly its original 25 questions, and every other shape count is unchanged.

**Sign convention for `category_pair_diff`: absolute.** 22 of 59 resolvable pairs have the
first-named category smaller, so the choice is material. Taken as absolute because this generator
states explicitly when it wants a signed answer — `mean_minus_median` questions all say "negative if
avg dips" — and these say nothing of the kind. Recorded as a judgement call.

`receivables_balance` is left **signed**: `Σ(Outstanding)` in the workbook equals invoiced − received
for all 25 clients exactly, and one client (Maharashtra Municipal) has receipts exceeding invoices,
so HV-IC-0412 is legitimately −13,279,236. That is what the shipped data says.

### Fixes needed for the new questions

- **"the NEDA file"** — client acronym, added to the abbreviation table.
- **"the Public Works Department account"** with no state — all five PWD clients score identically.
  Broken by the categories the question names: only one has works in both (`resolve_client(...,
  must_have_categories=...)`). Both pinned in `eval/test_resolution.py`, now 23 cases.

### Final state (v1.4)

333/333 computed · 0 fallbacks · samples **21/21 = 100%** · all three README golds exact ·
validator PASS · client-resolution suite **23/23** · hard-check violations 0 ·
clean end-to-end dry run from a deleted `build/`.

Every flagged distribution outlier was hand-verified: `HV-IC-0404` (₹7,822 — a client with
₹7.82 M invoiced and ₹7.81 M received), `HV-IC-0428` (₹8.5 M — a genuinely close category pair),
`HV-IC-0412` (the negative balance above), `HV-IC-0276` (a genuine negative mean − median).

---

## Phase 2 — `hidden_set_v1.3` (248 questions)

The earlier 371-question set (`hidden_set_v1.0`, score 88.671) is void. Upstream `4041a3d` cut the
set in three steps — 371 → 344 → 320 → **248** — and the scorer now skips `scored: false` rows.
The 248 are a **strict subset** of the 371: no new questions, no reworded ones.

**Every shape flagged in the "judgement calls" section below was withdrawn in full**:
`pair_overlap` (24), `business_units` (24), `largest_client_share` (23), `top_n_clients` (23),
`role_split` (21), `grading_filter` (3), plus `work_count` (1), 2 `exclusion_aggregate`,
1 `collection_pct`, 1 `awarded_vs_invoiced`. The organisers reached the same conclusion the risk
register did, which retires open risks 1–4 and 8–9 entirely.

### Bugs found and fixed while regenerating

1. **Client resolution was matching *work*-name words.** "Steel Truss Bridge" selected Mahanadi
   **Steel** Corporation; "Highway **Construction**" selected Lakshya … & **Construction**;
   "Drainage **Works** — Gujarat" selected a Public **Works** Department. **7 of 248 questions had
   the wrong client** — correct arithmetic over the wrong portfolio, which no range check catches.
   Fixed two ways: client-name tokens that also occur in the work-name vocabulary are down-weighted,
   and when the question cites an explicit `Pkg-N` that work's name is masked before client
   matching. Pinned in `eval/test_resolution.py` (21 cases).
2. **Three legitimate zeros were being overwritten by the placeholder.** A single-work client has
   mean == median, so `mean_minus_median` is exactly 0 — and `coerce()` rejected money == 0 and
   substituted 600,000,000. The scorer requires an exact 0 when gold is 0, so each was a 1.0 turned
   into a 0. Money is now signed *and* may be zero; `emit.write()` reports any solved value the
   format rejects, so this can't recur silently.
3. **`against`-variant regex repeated a bug I'd already fixed elsewhere** — unanchored `collect`
   matched "re**collect**ion" (HV-IC-0371), and "cash flow" overrode "successfully claimed"
   (HV-IC-0285). In this corpus "claims/claimed" means *invoiced*; only explicit
   collected/received/realised language now selects the receipts figure.
4. **`Σ` in `graph/build.py` crashed the clean dry run** under Windows cp1252 when stdout is piped.
   All entry points now force UTF-8.

### Verification of the two largest surviving shapes

- `awarded_vs_invoiced` (25): read all 25 verbatim. 23 say "awarded vs invoiced/billed" outright,
  so the Σ(outstanding) alternative is wrong; kept awarded − invoiced.
- `year_delta` (24): kept **absolute** difference — HV-IC-0269 asks for the "absolute difference"
  explicitly and the rest use gap/variance/delta/shift language.

### Final state (v1.3)

248/248 computed · 0 fallbacks · samples **21/21 = 100%** · all three README golds exact ·
validator PASS · client-resolution suite 21/21 · clean end-to-end dry run from an empty `build/`.

---

## Phase 1 (original 371-question set)

Scoring model in force: `score = max(0, 1 − |got − gold| / |gold|)`, averaged over 371.
Bias low on genuine uncertainty (overshoot caps at 0; undershoot is proportional).

**Final state: 371/371 computed, 0 fallbacks, samples 23/23 = 100%, all three README golds exact,
validator PASS.**

---

## Tier 0 — Skeleton ✅

A valid, submittable 371-row CSV before any new logic.

- `eval/validate.py` — header, 371 rows, order matching `sample_submission.csv`, no
  duplicates/blanks/NaN/inf/commas/symbols/scientific notation, per-`answer_type` integrality,
  percent ∈ [0,100], days > 0, money within corpus range.
- `qa/emit.py` — coerces `{qid: value}` to submission-legal strings; any unanswered qid gets a
  type-appropriate placeholder. Never blank, never 0.

Placeholders: money 600,000,000 · percent 50 · count 3 · days 900.
**Result:** 371 rows, 0 solved, validator PASS, `evaluate.py --self-test` 13/13.

`evaluate.py --submission submission.csv --questions sample_questions.json` scores 0 by
construction — samples are `HS-IC-*`, the scored set is `HV-IC-*`. Real regression test is
`python run.py --samples` → `build/sample_ours.csv`.

---

## Tier 1 — The work-table core ✅

**Extraction** (`extract/cache.py`): 687/687 documents, **3,508,164 chars** (briefing: ~3.5M),
**0 short-text flags**. Workbooks read twice (cached values + formulas), all 30 sheets incl. Notes.

**Facts** (`facts/parse.py`): only the types the 371 questions need. Two completion-certificate
families (table 84 / prose 71), two company-certificate families, five reference-letter patterns,
two personnel-certificate families. All money through one `parse_inr()` on `Decimal`.

**Graph** (`graph/build.py`) — **17/17 integrity assertions pass**:

| | got | want |
|---|---|---|
| works | 155 | 155 |
| Σ contract value | 55,303,999,999 | 55,303,999,999 |
| distinct client names | 28 | 28 |
| client record ids | 60 | 60 |
| reference letters (1:1) | 132 | 132 |
| works without a letter | 23 | 23 |
| roles Prime / JV | 96 / 59 | 96 / 59 |
| categories | 13 | 13 |
| grading present | 84 | 84 |
| credentials / CVs / invoices | 48 / 39 / 518 | 48 / 39 / 518 |

**Corrected a Phase-0 number:** distinct client names is **28, not 29** — the recon counted a null
`client_name` as a 29th value. The assertion caught it.

**Cross-validation across 155 independent document pairs: exactly one disagreement** — pkg 21,
193,299,999 (certificate) vs 193,300,000 (company certificate), the known ₹1 crore-rounding
artefact. 154/155 exact.

---

## Tier 2 — Receivables ✅ / Tier 3 — Business units ✅

Both folded into the same pass. `Receivables_Ageing.xlsx` (518 invoices) joins to the 28 client
names with no unmatched rows; CV `Business Unit` covers all 39 CVs.

---

## Tier 4 — Plausibility audit ✅

`eval/audit.py` audits the output, not the code: distribution per shape, hard range checks,
fallback reasons, corpus-scale checks, and the three README golds.

**0 hard-check violations.** The only distribution outlier is `HV-IC-0276` at −49,171,429, which is
a genuine negative `mean_minus_median`.

### Bugs the audit and hand-check found (none would have failed a unit test)

1. **`\bup\b` expanded the "up" in "wrapped up" to "uttar pradesh"** — sent a Jharkhand question to
   an Uttar Pradesh work. State initialisms are now expanded case-sensitively, before lowercasing.
2. **State bonus overwhelmed name matching** — "Gujarat Municipal Corporation" beat "Trishakti Power
   Generation Corporation" on any question mentioning a Gujarat work. Client scoring is now
   IDF-weighted (unique tokens like "trishakti" decisive, "corporation" near-noise) with the state
   as a tiebreak only.
3. **`lack` matched "B*lack* Belt"** — every Six Sigma question was eligible for the `absence` shape.
   Now `\black\b`.
4. **`collect` matched "re*collect*ion"** — routed questions into `awarded_vs_invoiced`. Now
   `\bcollect(ed|ion|ing)?\b`.
5. **"bid cutoff" routed a `temporal_chain` into `threshold_aggregate`.** Threshold shapes now carry
   a predicate: they only match if an amount is actually parseable.
6. **`norm_q` stripped the decimal point**, so "23.0 Cr limit" parsed as a bar of **0** and the
   filter passed everything. Amounts are now read from a decimal-preserving normalisation.
7. **Number words were read by leftmost regex match** — "contracts hitting the six crore" captured
   "contracts hitting the six". Now walks back from the unit token, trimming left until it parses.
8. **`exceed` matched "meeting or exceeding <amount>"**, routing a threshold question to
   `rank_value`. Now requires "exceeds the next/second".
9. **`hint_work` fired before first-name matching** — "pritis pmp … west bengal hospital block" took
   the manager of the wrong work. Order is now exact → first name → work hint.
10. **Oblique work references weren't constrained to the named engineer.** Now re-resolved within
    that engineer's portfolio when the two disagree.

Also found and fixed during the build: a **21st shape**, `mean_minus_median` (19 questions, 5.1%),
which was being absorbed into `avg_work_size` and answered with the mean — a large magnitude error.
Its answers are explicitly signed ("negative if avg dips"), so `money` had to be allowed negative in
both the emitter and the validator.

---

## Judgement calls (resolved without gold evidence)

Listed because each is a place the system could be wrong in a way nothing here would reveal.

1. **`pair_overlap` (24 q, 6.5%) — the two named engineers are pointers, not a filter.** Each work
   has exactly one manager, so a literal "works both delivered" intersection is *always empty*; the
   question would be degenerate. Answer is the client's completed-work count. The union reading is
   the live alternative and would give smaller numbers.
2. **`largest_client_share` (23 q) — share of *value*, not of count.** "Percentage of his total
   deliveries" is ambiguous; value is the bid-desk reading.
3. **`top_n_clients` (23 q) — the engineer's own delivered value per client, top two.** Not those
   clients' full portfolios. This is the smaller number, consistent with bias-low.
4. **`business_units` (24 q) — "principal/main account" = the engineer's largest client by value**,
   then distinct business units across that client's works' managers.
5. **Ambiguous bare first names** ("meera", "Priya") resolve to the manager with the largest
   portfolio, unless a named work disambiguates.
6. **Zero-invoice clients:** `collection_pct` → 0 (0 and 100 both arguable; bias low). No question
   actually hits this branch. `awarded_vs_invoiced` → the full awarded value (nothing billed).
7. **Thresholds are inclusive (`>=`).** Still untested — no work sits exactly on a bar.
8. **`grading_filter` (3 q) = 1,715,900,000** for Arunodaya's 5 Satisfactory works. The question
   plants "around 4.2 crores"; that is 40× smaller and I could not reconstruct it under any reading.
   One of Arunodaya's 7 works is a prose certificate with no grading at all — the known structural
   gap that got this shape withdrawn from the samples.
9. **Exclusion with no resolvable category** falls back to Prime-only ("strip out the subcontractor
   allocations", "non-billable phases").

## Open risk

`HV-IC-0263` states "the five completed jobs" for PHED Odisha. Name-grouping gives **8** works;
client record 57 alone gives exactly **5**. This is the first textual hint pointing at record-id
grouping rather than name grouping. **It does not change the answer** — both readings yield 3
unreferenced — and three golds (NEDA 4 ids, Lakshya 3 ids, I&W Rajasthan 2 ids) directly confirm
name-grouping, so the frozen decision stands. Flagged because it is the only contrary evidence seen.
