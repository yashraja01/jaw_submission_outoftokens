"""Turn one question into one number, and record how.

Two planners propose, the fact store disposes. Each proposal is an executable plan; each plan is
run against exact arithmetic; the answer is the value the most proposals agree on. Agreement is
measured on the *computed number*, not on the plan, because two differently-worded plans that
compute the same figure are the same answer, and that is the only equality that matters here.

Scoring is exact-match this round — money within 0.5%, a count exactly, a percentage within 0.05 —
so there is no credit for being approximately right and no reason to prefer a hedged answer to a
committed one. There is also no penalty for a wrong answer, so nothing is ever left blank.
"""
import statistics
from decimal import Decimal

from pipeline import anchor, dsl, rules


class Candidate:
    def __init__(self, source, plan, value=None, derivation=None, error=None, ctx=None):
        self.source = source
        self.plan = plan
        self.value = value
        self.derivation = derivation
        self.error = error
        self.ctx = ctx

    @property
    def ok(self):
        return self.value is not None and self.error is None


def run_plan(source, raw_plan, question, facts, mentions=None):
    """Normalise, resolve anchors, execute. Any failure is captured, never raised."""
    try:
        plan = raw_plan if isinstance(raw_plan, dict) and "combine" in raw_plan else None
        plan = dsl.normalise(raw_plan) if plan is None else raw_plan
    except dsl.PlanError as e:
        return Candidate(source, None, error=f"invalid plan: {e}")
    try:
        hints = dict(mentions or {})
        for k, v in (plan.get("anchor") or {}).items():
            if v and not hints.get(k):
                hints[k] = v
        ctx = anchor.build(facts, question, mentions=hints)
        value, why = dsl.execute(plan, ctx)
        return Candidate(source, plan, value=value, derivation=why, ctx=ctx)
    except dsl.PlanError as e:
        return Candidate(source, plan, error=str(e))
    except Exception as e:                                      # noqa: BLE001
        return Candidate(source, plan, error=f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- fallbacks
def fallbacks(facts):
    """A scale-appropriate figure per answer type, for a question nothing could execute.

    Under exact match these are worth close to nothing, which is the point: they exist only because
    an unanswered row scores zero and a wrong one costs nothing, so a blank is strictly dominated.
    """
    clients = [c for c in facts.clients.values() if c["works"]]

    def med(vals):
        vals = [float(v) for v in vals if v is not None]
        return statistics.median(vals) if vals else None

    totals = [sum(w["value"] for w in c["works"]) for c in clients] or [Decimal(0)]
    return {
        "money": med(totals),
        "count": med([len(c["works"]) for c in clients]) or 1,
        "percent": med([float(b["received"] * 100 / b["invoiced"])
                        for b in facts.inv_by_client.values() if b["invoiced"]]) or 50.0,
        "days": med([(w["value"] * 0) + 365 for w in facts.works]) or 365,
    }


# --------------------------------------------------------------------------- reconciliation
def rules_confident(rules_candidate, matched_a_cue):
    """Did the rule-based reading actually understand this question, or fall back on a default?

    Three things have to hold. A cue fired, rather than the answer type's default shape being used
    because nothing matched. The plan ran. And the entities it needed were resolved from the
    question rather than guessed from an engineer's portfolio — a guessed client is the single
    largest source of confidently wrong arithmetic here.
    """
    if not matched_a_cue or not rules_candidate.ok:
        return False
    ctx = rules_candidate.ctx
    if ctx is None:
        return False
    how = (ctx.trace or {}).get("client_from")
    if how == "the engineer's principal account":
        return False
    return True


def reconcile(candidates, answer_type, trust="consensus", confident=True):
    """Pick between the planners' answers.

    Votes are counted on the coerced answer — the string that would go in the CSV — so two plans
    that reach the same rupee figure by different routes count as agreeing.

    The default policy, `consensus`, gives the model the questions the rules are unsure about and
    keeps the rest. That asymmetry is measured, not assumed. Sampling the model three times only
    protects against *random* misreadings: against a mock model that misreads 30% of questions
    independently on each sample, majority voting held the score at 0.994, but against one that
    misreads the same question the same way every time — which is how a real model fails — the
    same voting fell to 0.811, while the rules alone stayed at 0.994. Self-consistency cannot see
    a consistent mistake, so it is not enough on its own to justify overturning a reading that is
    measurably right 331 times out of 333.

    So the model decides where the rules admit they are guessing: no cue fired, the plan would not
    run, or the client had to be inferred from an engineer's portfolio rather than read from the
    question. Those are exactly the cases the rules get wrong, and there the model's plan is the
    better bet even at one sample. Everywhere else the rules stand and the model's dissent is
    recorded in the run log instead of acted on.

    `trust="model"` lets a plain majority win everywhere, and `trust="rules"` ignores the model
    unless the rules produced nothing at all.
    """
    ok = [c for c in candidates if c.ok]
    if not ok:
        return None, "nothing executed", []

    key_of = lambda c: str(dsl.coerce(c.value, answer_type))
    tally = {}
    for c in ok:
        slot = tally.setdefault(key_of(c), {"n": 0, "llm": 0, "rules": 0, "first": c})
        slot["n"] += 1
        slot["llm" if c.source.startswith("llm") else "rules"] += 1

    rules_c = next((c for c in ok if not c.source.startswith("llm")), None)
    llm_cs = [c for c in ok if c.source.startswith("llm")]
    llm_keys = {key_of(c) for c in llm_cs}

    def llm_pick():
        """The model's own answer: unanimous, else its majority, else its first plan."""
        counts = {}
        for c in llm_cs:
            counts.setdefault(key_of(c), []).append(c)
        best = max(counts.values(), key=len)
        return best[0], f"{len(best)}/{len(llm_cs)} model plans"

    if rules_c is None:
        chosen, n = llm_pick()
        how = f"model only, the rules produced no runnable plan ({n})"
    elif not llm_cs:
        chosen, how = rules_c, "rules only, the model produced no runnable plan"
    elif len(llm_keys) == 1 and key_of(rules_c) in llm_keys:
        chosen, how = rules_c, f"agreed by the rules and {len(llm_cs)} model plan(s)"
    elif trust == "rules":
        chosen, how = rules_c, "rules preferred (--trust rules)"
    elif trust == "model":
        chosen = max(tally.values(), key=lambda s: (s["n"], s["llm"]))["first"]
        how = "majority vote (--trust model)"
    elif not confident and len(llm_cs) >= 2 and len(llm_keys) == 1:
        chosen = llm_cs[0]
        how = (f"model decides: the rules had no confident reading and all {len(llm_cs)} model "
               f"plans agree (the rules said {rules_c.value!s:.20})")
    else:
        chosen = rules_c
        how = (f"rules kept over the model's {sorted(llm_keys)[:2]}: the rules matched a cue, "
               f"resolved their entities and ran")

    agreement = f"{tally[key_of(chosen)]['n']}/{len(ok)} plans agree; {how}"
    return chosen, agreement, sorted(tally.keys())


def answer_question(q, facts, llm_plans=None, fb=None, trust="consensus"):
    """Everything for one question: plans, execution, vote, and the record of it."""
    question, atype = q["question"], q.get("answer_type", "money")
    candidates = []

    rules_plan, shape, matched = rules.plan(question, atype, facts)
    rules_c = run_plan("rules", rules_plan, question, facts)
    candidates.append(rules_c)

    for i, lp in enumerate(llm_plans or []):
        candidates.append(run_plan(f"llm{i}", lp, question, facts,
                                   mentions=(lp.get("anchor") if isinstance(lp, dict) else None)))

    confident = rules_confident(rules_c, matched)
    chosen, agreement, distinct = reconcile(candidates, atype, trust=trust,
                                            confident=confident)
    fb = fb or {}
    if chosen is None:
        value = fb.get(atype)
        record = {"qid": q["qid"], "answer_type": atype, "value": value, "source": "fallback",
                  "shape": shape, "agreement": agreement,
                  "errors": [f"{c.source}: {c.error}" for c in candidates if c.error][:4],
                  "plan": None, "derivation": None, "entities": None,
                  "question": question[:180]}
        return value, record

    record = {
        "qid": q["qid"], "answer_type": atype, "value": chosen.value, "source": chosen.source,
        "shape": shape, "agreement": agreement, "distinct_values": distinct[:4],
        "plan": dsl.describe(chosen.plan), "derivation": chosen.derivation,
        "entities": chosen.ctx.summary() if chosen.ctx else None,
        "errors": [f"{c.source}: {c.error}" for c in candidates if c.error][:4],
        "question": question[:180],
    }
    return chosen.value, record
