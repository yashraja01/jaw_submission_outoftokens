"""Read each question with the language model, and get back a plan rather than a number.

The division of labour is the whole design. The model is good at what the rules are bad at —
reading an unfamiliar sentence and working out that "what's left once we set the water treatment
side aside" is an exclusion and "the slice sitting with her biggest account" is a share of a total.
It is not good at adding up 19 ten-digit rupee figures without slipping a digit, and this round is
scored on exact match. So it never sees the numbers and never returns one: it returns a plan, the
fact store executes it, and every rupee of arithmetic happens in `Decimal`.

The corpus vocabulary in the prompt — client names, engineer names, the exact category labels — is
read out of the fact store at run time. Nothing about any particular estate is written here.
"""
import concurrent.futures as cf
import json
import re
import time

from pipeline import dsl
from pipeline.llm import Endpoint

# --------------------------------------------------------------------------- wire schema
#
# Flat, enumerated and free of `anyOf`/`$ref`: constrained decoding is happiest with a plain
# object, and every field the model can write is one we can validate. Absent is expressed as the
# empty string or the "none" source, never as null, so the schema needs no nullable types.
MEASURE = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "enum": ["works", "invoices", "constant", "none"]},
        "scope": {"type": "string", "enum": ["client", "engineer", "work", "corpus"]},
        "agg": {"type": "string", "enum": list(dsl.AGGS) + [""]},
        "n": {"type": "integer"},
        "value": {"type": "number"},
        "categories_in": {"type": "array", "items": {"type": "string"}},
        "categories_not_in": {"type": "array", "items": {"type": "string"}},
        "role": {"type": "string"},
        "grading": {"type": "string"},
        "state": {"type": "string"},
        "years_in": {"type": "array", "items": {"type": "integer"}},
        "completed_after": {"type": "string"},
        "completed_before": {"type": "string"},
        "min_value": {"type": "number"},
        "max_value": {"type": "number"},
        "has_reference_letter": {"type": "string", "enum": ["yes", "no", "any"]},
    },
    "required": ["source", "scope", "agg"],
}

SCHEMA = {
    "type": "object",
    "properties": {
        "reading": {"type": "string"},
        "anchor_client": {"type": "string"},
        "anchor_engineer": {"type": "string"},
        "anchor_work": {"type": "string"},
        "left": MEASURE,
        "right": MEASURE,
        "combine": {"type": "string", "enum": list(dsl.COMBINES)},
    },
    "required": ["reading", "anchor_client", "anchor_engineer", "anchor_work",
                 "left", "right", "combine"],
}

SPEC = """You convert a bid-desk question into a PLAN over a fact store. You never do arithmetic
and never state a number as the answer: the plan is executed for you, exactly.

THE FACT STORE
  works     one row per completed work: value (rupees, from the client's completion certificate),
            completion date, category, state, client, project manager (engineer), Prime/JV role,
            the client's grading, and whether a client reference letter is on file for it.
  invoices  one row per invoice: client, date, invoiced, received, outstanding.

A PLAN
  anchor_client / anchor_engineer / anchor_work
      Copy the span of the question that names each one, or "" if it names none. Copy it as
      written; do not correct or expand it. These only tell the resolver where to look.
  left, right   measures (see below). Set right.source to "none" when one measure answers it.
  combine       left            -> the left measure alone
                difference      -> left minus right (signed; use when the question wants a signed
                                   gap, e.g. "negative if the average is lower")
                abs_difference  -> the size of the gap between left and right, unsigned
                percent         -> 100 * left / right
                sum             -> left plus right

A MEASURE
  source  works | invoices | constant | none
  scope   client   -> every work of the client the question is about (the usual case)
          engineer -> every work that engineer led, across all their clients
          work     -> the single work named
          corpus   -> every work in the estate, all clients (use only when the question really is
                      about the whole business, e.g. "across the entire portfolio")
  agg over works
          sum_value mean_value median_value count max_value min_value
          nth_largest_value (with n)      top_n_sum_by_client (with n)
          max_client_value               distinct_categories distinct_clients distinct_managers
          distinct_business_units distinct_states
          days_since_anchor_date  (scope must be work: days from the named certificate's issue
                                   date to that work's completion)
  agg over invoices
          sum_invoiced sum_received sum_outstanding count_invoices
  filters (all optional, applied before the aggregate)
          categories_in / categories_not_in : exact category labels from the list below
          role : Prime | JV Partner | Sub-contractor
          years_in : completion years
          completed_after / completed_before : YYYY-MM-DD, or "anchor" for the date of the
              certificate the question names
          min_value / max_value : rupees
          has_reference_letter : yes | no | any
          grading, state
  constant : set value, for a target figure the question states ("clear the 120 Cr threshold")

RULES
  * Money is rupees. 1 crore = 10,000,000; 1 lakh = 100,000.
  * "collection percentage / how much of what we billed has come in" = percent(sum_received,
    sum_invoiced) over invoices.
  * "what do they still owe" = difference(sum_invoiced, sum_received) over invoices.
  * "awarded versus what we billed" = abs_difference(sum_value over works, sum_invoiced over
    invoices) — one side is works, the other invoices.
  * "share of works with a reference letter" = percent(count with has_reference_letter yes,
    count with any).
  * "how many works have no reference letter" = count with has_reference_letter no.
  * An engineer named with a client is usually a pointer to that client; scope stays client unless
    the question asks about the engineer's own portfolio across clients.
  * Prefer the smallest plan that answers the question exactly. Do not add filters the question
    does not ask for.
  * reading: one short sentence saying what is being computed, in your own words."""

EXAMPLES = [
    ({"q": "For the Northern Canal Authority, what's the combined value of everything we've "
           "finished for them, leaving the pipeline work out of it?",
      "answer_type": "money"},
     {"reading": "Total value of the client's completed works excluding one category.",
      "anchor_client": "Northern Canal Authority", "anchor_engineer": "", "anchor_work": "",
      "left": {"source": "works", "scope": "client", "agg": "sum_value",
               "categories_not_in": ["pipelines"]},
      "right": {"source": "none", "scope": "client", "agg": ""},
      "combine": "left"}),
    ({"q": "Priya Nair's PMP (PMI-200031) — of everything she has run, what percentage of the "
           "value sits with her single biggest client?",
      "answer_type": "percent"},
     {"reading": "Her largest client's value as a percentage of her whole portfolio.",
      "anchor_client": "", "anchor_engineer": "Priya Nair", "anchor_work": "",
      "left": {"source": "works", "scope": "engineer", "agg": "max_client_value"},
      "right": {"source": "works", "scope": "engineer", "agg": "sum_value"},
      "combine": "percent"}),
    ({"q": "Since her Six Sigma certificate was issued, how much has Meera Roy delivered?",
      "answer_type": "money"},
     {"reading": "Value of her works completed after the certificate's issue date.",
      "anchor_client": "", "anchor_engineer": "Meera Roy", "anchor_work": "",
      "left": {"source": "works", "scope": "engineer", "agg": "sum_value",
               "completed_after": "anchor"},
      "right": {"source": "none", "scope": "client", "agg": ""},
      "combine": "left"}),
    ({"q": "State Water Board: how far apart are the drainage and the water treatment totals?",
      "answer_type": "money"},
     {"reading": "Gap between two categories' totals for one client.",
      "anchor_client": "State Water Board", "anchor_engineer": "", "anchor_work": "",
      "left": {"source": "works", "scope": "client", "agg": "sum_value",
               "categories_in": ["drainage"]},
      "right": {"source": "works", "scope": "client", "agg": "sum_value",
                "categories_in": ["water treatment"]},
      "combine": "abs_difference"}),
]


def vocabulary(facts, max_names=90):
    """What this estate actually contains, so the model names things the way the store does."""
    clients = sorted({c["name"] for c in facts.clients.values() if c["name"]})
    engineers = sorted({m["name"] for m in facts.managers.values() if m["name"]})
    gradings = sorted({(w["grading"] or "").strip() for w in facts.works if w["grading"]})
    years = sorted({w["year"] for w in facts.works if w["year"]})
    roles = sorted({w["role"] for w in facts.works if w["role"]})

    def block(name, items):
        items = list(items)
        shown = items[:max_names]
        tail = "" if len(items) <= max_names else f" ... (+{len(items) - max_names} more)"
        return f"{name} ({len(items)}): " + "; ".join(shown) + tail

    return "\n".join([
        "THIS ESTATE",
        block("category labels, use these exactly", facts.categories),
        block("roles", roles),
        block("clients", clients),
        block("engineers", engineers),
        block("gradings recorded", gradings) if gradings else "gradings recorded: none",
        f"completion years: {years[0]}-{years[-1]}" if years else "completion years: unknown",
    ])


def build_messages(question, answer_type, vocab, tier=None):
    shots = []
    for q, plan in EXAMPLES:
        shots.append({"role": "user",
                      "content": f"answer_type: {q['answer_type']}\nquestion: {q['q']}"})
        shots.append({"role": "assistant", "content": json.dumps(plan)})
    ask = f"answer_type: {answer_type}\n"
    if tier:
        ask += f"tier: {tier}\n"
    ask += f"question: {question}"
    return ([{"role": "system", "content": SPEC + "\n\n" + vocab}] + shots
            + [{"role": "user", "content": ask}])


# --------------------------------------------------------------------------- wire -> plan
def _snap_category(label, facts):
    """Map the model's words onto a label the store actually holds, or drop the filter.

    A category filter is matched exactly, so "water treatment plants" against the label "water
    treatment" would silently select nothing and hand back a confident zero. Snapping first, and
    dropping what will not snap, turns that into a plan we can reject.
    """
    s = re.sub(r"[^a-z0-9 ]+", " ", str(label).lower())
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None
    for c in facts.categories:
        if c == s:
            return c
    for c in facts.categories:
        if s in c or c in s:
            return c
    want = set(s.split())
    best, score = None, 0.0
    for c in facts.categories:
        toks = set(c.split())
        overlap = len(want & toks) / max(len(toks), 1)
        if overlap > score:
            best, score = c, overlap
    return best if score >= 0.5 else None


def _snap_role(role, facts):
    s = str(role).strip().lower()
    if not s:
        return None
    known = {(w["role"] or "").lower(): w["role"] for w in facts.works if w["role"]}
    if s in known:
        return known[s]
    if "jv" in s or "joint" in s:
        return known.get("jv partner", "JV Partner")
    if "sub" in s:
        return known.get("sub-contractor", "Sub-contractor")
    if "prime" in s or "lead" in s:
        return known.get("prime", "Prime")
    return None


def _measure(w, facts):
    if not isinstance(w, dict):
        return None
    src = (w.get("source") or "none").strip()
    if src == "none":
        return None
    if src == "constant":
        return {"source": "constant", "value": w.get("value")}

    filters = {}
    for key in ("categories_in", "categories_not_in"):
        vals = [_snap_category(c, facts) for c in (w.get(key) or [])]
        vals = [v for v in vals if v]
        if vals:
            filters[key] = sorted(set(vals))
    role = _snap_role(w.get("role") or "", facts)
    if role:
        filters["role"] = role
    if w.get("grading"):
        filters["grading"] = w["grading"]
    if w.get("state"):
        filters["state"] = w["state"]
    if w.get("years_in"):
        filters["years_in"] = w["years_in"]
    for k in ("completed_after", "completed_before"):
        if w.get(k):
            filters[k] = w[k]
    for k in ("min_value", "max_value"):
        if w.get(k) not in (None, 0):
            filters[k] = w[k]
    ref = (w.get("has_reference_letter") or "any").strip().lower()
    if ref in ("yes", "no"):
        filters["has_reference_letter"] = (ref == "yes")

    return {"source": src, "scope": w.get("scope") or "client", "agg": w.get("agg") or "",
            "n": w.get("n") or 2, "filters": filters}


def to_plan(wire, facts):
    """Validated plan, or None. A plan we cannot validate is dropped, never patched."""
    if not isinstance(wire, dict):
        return None
    raw = {
        "anchor": {"client": wire.get("anchor_client") or None,
                   "engineer": wire.get("anchor_engineer") or None,
                   "work": wire.get("anchor_work") or None},
        "left": _measure(wire.get("left"), facts),
        "right": _measure(wire.get("right"), facts),
        "combine": wire.get("combine") or "left",
        "note": (wire.get("reading") or "")[:200],
    }
    if raw["left"] is None:
        return None
    if raw["combine"] != "left" and raw["right"] is None:
        return None
    try:
        return dsl.normalise(raw)
    except dsl.PlanError:
        return None


# --------------------------------------------------------------------------- batch
def plan_all(questions, facts, samples=3, concurrency=8, budget_seconds=2400,
             endpoint=None, log=print):
    """{qid: [plan, ...]} for every question the model managed to read in the time available.

    Sample 0 is greedy; the rest are sampled, so that agreement between them means something. A
    question the model cannot be reached for simply has no plans, and the rule-based planner
    answers it — the run never stops for the endpoint.
    """
    ep = endpoint or Endpoint(log=log)
    if not ep.probe():
        return {}
    vocab = vocabulary(facts)
    log(f"[plan] {len(questions)} questions x {samples} samples, {concurrency} in flight, "
        f"budget {budget_seconds}s")

    t0 = time.time()
    out = {}
    jobs = [(q, i) for q in questions for i in range(samples)]

    def work(job):
        q, i = job
        if time.time() - t0 > budget_seconds:
            return q["qid"], None
        msgs = build_messages(q["question"], q.get("answer_type", "money"), vocab, q.get("tier"))
        text = ep.chat(msgs, temperature=0.0 if i == 0 else 0.7, schema=SCHEMA)
        if not text:
            return q["qid"], None
        try:
            wire = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)               # a stray prose wrapper
            if not m:
                return q["qid"], None
            try:
                wire = json.loads(m.group(0))
            except json.JSONDecodeError:
                return q["qid"], None
        return q["qid"], to_plan(wire, facts)

    done = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        for qid, plan in ex.map(work, jobs):
            done += 1
            if plan is not None:
                out.setdefault(qid, []).append(plan)
            if done % 100 == 0 or done == len(jobs):
                log(f"[plan]   {done}/{len(jobs)} plans requested "
                    f"({time.time() - t0:.0f}s elapsed)")

    covered = sum(1 for q in questions if out.get(q["qid"]))
    log(f"[plan] usable plans for {covered}/{len(questions)} questions; "
        f"endpoint {ep.stats()}")
    if time.time() - t0 > budget_seconds:
        log("[plan] NOTE the planning budget ran out; the remaining questions are answered by "
            "the rule-based planner alone")
    return out
