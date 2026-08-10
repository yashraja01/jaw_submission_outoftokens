# Build log — Phase 1

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
