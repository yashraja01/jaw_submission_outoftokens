"""A model-free planner: read the question's structure, emit the same plan the model emits.

This is the floor the system stands on. It exists for three reasons: the endpoint may be slow,
throttled or down; a structured plan the model returns still has to be sanity-checked against
something; and a question the model reads oddly is usually one whose *shape* is still obvious from
its grammar.

What it is not is a phrase list. An earlier version of this classifier was fitted to the phrasings
of a question set we had been given — 102 alternatives, 71 of them carrying two questions or fewer.
That precision is exactly what does not survive a re-worded question: measured by removing the
alternative each question matched on, the fitted rules scored 0.232. Everything here is written
from what each shape *means* instead — stems rather than whole phrases (`referenc` covers
reference/referenced/referencing), and structural cues that do not depend on wording at all: two
years named, a parseable rupee amount, two category labels, a superlative with a runner-up.
"""
import re

from pipeline import dsl
from pipeline.facts import norm_q

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}


def words_to_number(s):
    total, cur, seen = 0, 0, False
    for t in re.split(r"[\s-]+", s.strip().lower()):
        if t in UNITS:
            cur += UNITS[t]
            seen = True
        elif t in TENS:
            cur += TENS[t]
            seen = True
        elif t == "hundred" and seen:
            cur *= 100
        else:
            return None
    return total + cur if seen else None


def norm_amt(text):
    """Lowercase but keep decimal points: the general normaliser strips them, turning '23.0 Cr'
    into '23 0 cr' and making the amount parse as zero."""
    s = re.sub(r"[’‘]", "'", str(text)).lower()
    s = s.replace(",", "").replace("&", " and ")
    s = re.sub(r"[^a-z0-9. -]+", " ", s)
    s = re.sub(r"(\d)\s*([a-z])", r"\1 \2", s)      # "70cr" has no word boundary to match on
    return re.sub(r"\s+", " ", s)


def find_amount(nq):
    """A rupee threshold stated in words or digits: 'twenty-six crore', '70cr', 'INR 20 Cr'.

    Number words are read by walking back from each unit token and trimming from the left until the
    remainder parses; a leftmost regex match would otherwise swallow 'contracts hitting the six'.
    """
    for unit, mult in (("crores?|cr", 10_000_000), ("lakhs?|lacs?", 100_000)):
        for m in re.finditer(rf"\b(?:{unit})\b", nq):
            before = nq[:m.start()].strip()
            m2 = re.search(r"([\d.]+)\s*$", before)
            if m2:
                return int(float(m2.group(1)) * mult)
            words = re.split(r"[\s-]+", before)[-4:]
            while words:
                n = words_to_number(" ".join(words))
                if n:
                    return n * mult
                words = words[1:]
    return None


def years_in(text):
    ys = {int(y) for y in re.findall(r"\b(19\d\d|20[0-2]\d)\b", text)}
    return sorted(y for y in ys if 1990 <= y <= 2035)


GRADES = r"satisfactory|very good|excellent|good"

# Unambiguous endorsement language. Narrow on purpose: it is the *block* on reading a question as a
# receipts question, and a wide version ("verified", "approved", "support") blocks genuine
# collection questions — "the verified collection percentage once we factor in actual receipts" is
# about money, and the only endorsement word in it is a false friend.
REFLANG = (r"referenc|endors|testimoni|sign.?off|letter of recommend|reference letter|"
           r"formal verification|on file")

# The award side of a comparison. A question that names what was *awarded* and asks about a gap is
# comparing the award against the billing, not the billing against the receipts — whatever
# receivables vocabulary it also uses ("the outstanding balance against the total contract value").
AWARD_SIDE = (r"awarded|award value|contract value|contract totals|sanctioned|secured|committed|"
              r"approved contract|total scope|\bawards?\b|project value|handed over|commitments|"
              r"unbilled|(total|full|entire) value of (the |their |our )?"
              r"(projects|works|assignments|jobs|contracts)")

# A target to be reached, rather than two figures both already known. "How much more do we need"
# is a gap however much receivables vocabulary surrounds it.
GAPLANG = (r"how much (more|additional|further|else)|still need|need to (reach|hit|clear|secure|"
           r"get|bring)|shortfall (to|against|towards)|fall short of|to (reach|clear|hit|secure)"
           r" (our|the|a)|make up|top up|more value do we need|remaining to (reach|hit)|"
           r"credential threshold|qualifying threshold|eligibility threshold")

# Ordered specific-first within each answer type. The first entry whose pattern matches and whose
# structural precondition holds wins.
SHAPES = [
    # ---- percent
    ("largest_client_share", "percent", r"largest|biggest|top\b|main|principal|primary|foremost|"
                                        r"dominant|single (client|account)|leading"),
    # Money first: its vocabulary (billed, receipts, collected) is specific, and it is blocked by
    # REFLANG when the thing being counted is endorsements rather than rupees.
    ("collection_pct",       "percent", r"collect|receipt|receiv|realis|realiz|recover|cash|"
                                        r"\bpaid\b|payment|bill|invoic|clear|settle"),
    # A client approving, accepting or clearing a work *is* the endorsement on file; these are the
    # words a bid desk uses for it, and they sit behind collection_pct so a genuine receipts
    # question that happens to say "approved" is unaffected.
    ("referenced_share",     "percent", r"referenc|endors|testimoni|sign.?off|recommend|"
                                        r"attest|vouch|formal verification|letter|approv|"
                                        r"accept(ed|ance)|clearance|commend|praise"),

    # ---- count
    ("business_units",       "count",   r"business unit|internal (unit|team|division)|"
                                        r"\bdivision|\bunits\b"),
    ("absence",              "count",   r"\black\b|lacking|missing|absent|without|unreferenced|"
                                        r"no (client )?(reference|letter|testimonial|endorsement)|"
                                        r"do(es)? not have|don.?t have|never (received|got)|"
                                        r"yet to (receive|be)"),
    ("distinct_category",    "count",   r"categor|classification|work type|types? of work|"
                                        r"kinds? of work|different (kinds|types)|discipline"),
    ("distinct_clients",     "count",   r"how many (distinct |different |separate )?"
                                        r"(clients|departments|authorities|customers)"),
    ("work_count",           "count",   r"."),

    # ---- money, specific-first
    ("mean_minus_median",    "money",   r"\bmedian\b"),
    ("category_pair_diff",   "money",   r"."),                  # gated on two labels being named
    # A named target plus gap language outranks the receivables reading: "the outstanding contract
    # value we still need to secure to clear the 120 Cr threshold" is arithmetic against 120 Cr.
    ("gap_to_threshold",     "money",   r"how much (more|additional|further|else)|shortfall|"
                                        r"short of|still need|need to (reach|hit|clear|secure|get)|"
                                        r"to (reach|clear|hit|secure) (our|the|a)|target|"
                                        r"remaining to|make up|top up|fall short|threshold"),
    ("receivables_balance",  "money",   r"\bowe|\bunpaid\b|outstand|\bpending\b|arrears|overdue|"
                                        r"still (due|owed|open|to be (paid|collected|settled))|"
                                        r"not been (paid|settled|cleared)|"
                                        r"yet to (pay|be paid|collect|clear|settle)|"
                                        r"remain(s|ing)? (on|due|unpaid|outstanding|owing|to be)|"
                                        r"(amount|balance|sum|value) (still |currently )?remain|"
                                        r"(currently|still|now) due|amount due|due (across|from|on)|"
                                        r"balance\b"),
    ("grading_filter",       "money",   r"grad(e|ed|ing)|rated|assessment|remark"),
    ("year_delta",           "money",   r"."),                  # gated on two years being named
    ("awarded_vs_invoiced",  "money",   r"invoic|\bbill|claim|raised against|submitted for payment|"
                                        r"vs award|against[^.?]{0,25}(award|contract value|"
                                        r"sanction|total scope)"),
    ("top_n_clients",        "money",   r"top (two|three|2|3)|(two|three|2|3) (largest|biggest)|"
                                        r"(largest|biggest) (two|three)"),
    # a superlative and a runner-up, in either order and at any distance
    ("rank_value",           "money",   r"(?=.*(largest|biggest|highest|top\b|greatest))"
                                        r"(?=.*(second|\bnext\b|runner|one down|subsequent|"
                                        r"behind|below (it|that)))"),
    ("exclusion_aggregate",  "money",   r"exclud|except|without|net of|minus|less the|leav(e|ing)|"
                                        r"drop|strip|remove|removing|set aside|ignor|other than|"
                                        r"besides|apart from|discount(ing)? the|bar the|omit|"
                                        r"filter(ing)? out|take out|taking out|not counting|"
                                        r"barring|save for|put aside|carve out"),
    ("threshold_aggregate",  "money",   r"."),                  # gated on an amount being named
    ("role_split",           "money",   r"\bprime\b|\bjv\b|joint venture|lead partner|"
                                        r"subcontract|sub-contract|in that capacity|as (the )?lead"),
    ("avg_work_size",        "money",   r"averag|\bmean\b|typical|per (project|work|assignment|job|"
                                        r"contract)|\bavg\b|on average"),
    ("temporal_chain",       "money",   r"after|since|subsequent|following|post[- ]|later than|"
                                        r"from that (date|point)|onward"),
    ("hop_aggregate",        "money",   r"."),

    # ---- days
    ("date_span",            "days",    r"."),
]

# Some shapes are only meaningful if the thing they operate on is actually present. Without these,
# filler like "before the bid cutoff" routes a date question into a value threshold.
PRECONDITIONS = {
    "threshold_aggregate": lambda raw, f: (find_amount(norm_amt(raw)) is not None
                                           and not re.search(GAPLANG, raw.lower())),
    # "the outstanding balance against the total contract value" names the award side, so it is an
    # award-versus-billing question and not an invoiced-minus-received one
    "receivables_balance": lambda raw, f: not re.search(AWARD_SIDE, raw.lower()),
    "awarded_vs_invoiced": lambda raw, f: re.search(AWARD_SIDE, raw.lower()) is not None,
    "gap_to_threshold": lambda raw, f: (find_amount(norm_amt(raw)) is not None
                                        and re.search(GAPLANG, raw.lower()) is not None),
    "year_delta": lambda raw, f: len(years_in(raw)) >= 2,
    "category_pair_diff": lambda raw, f: len(f.resolve_category_pair(raw, f.resolve_client(raw))) >= 2,
    "grading_filter": lambda raw, f: re.search(GRADES, raw.lower()) is not None,
    # a question that names the median is not asking for a plain average, whatever else it says
    "avg_work_size": lambda raw, f: not re.search(r"\bmedian\b", raw.lower()),
    # "cleared" is a receipts word everywhere except where what cleared is an endorsement
    "collection_pct": lambda raw, f: not re.search(REFLANG, raw.lower()),
}

DEFAULT_SHAPE = {"money": "hop_aggregate", "count": "work_count",
                 "percent": "collection_pct", "days": "date_span"}


# Corpus-wide language. A question about the whole business names no client at all, and without
# this the client resolver either picks one at random or fails - the systematic blind spot of a
# planner whose default scope is "the client this is about".
# "portfolio" is deliberately absent: a portfolio here is nearly always one client's or one
# engineer's ("the mean size across the client's full portfolio"), and reading it as the whole
# estate answers a question about one department with the sum of sixty. Corpus scope has to be
# said explicitly - all clients, every department, company-wide.
CORPUS_SCOPE = (r"all (?:of )?(?:our |the )?(?:clients|customers|departments|authorities)|"
                r"every (?:client|customer|department|authority)|"
                # deliberately not "every project": "across every project we've wrapped up for
                # that client" is one client's total, and the noun being quantified is what
                # separates the two readings
                r"company.?wide|firm.?wide|group.?wide|"
                r"across the (?:whole |entire )?(?:business|estate|company|group|"
                r"organisation|organization)|"
                r"(?:whole|entire) (?:business|estate|company)|"
                r"in aggregate across (?:all|every)")


def classify(question, answer_type, facts):
    """(shape, matched) - `matched` is False when nothing fired and a default had to be used."""
    raw = re.sub(r"\s+", " ", question).lower()
    nq = norm_q(question)
    for shape, at, pat in SHAPES:
        if at != answer_type:
            continue
        if not (re.search(pat, nq) or re.search(pat, raw)):
            continue
        pre = PRECONDITIONS.get(shape)
        if pre:
            try:
                if not pre(question, facts):
                    continue
            except Exception:                                # noqa: BLE001
                continue
        # A bare "." is not evidence of anything unless a precondition did the matching -
        # category_pair_diff is routed by two labels being present, not by any word.
        return shape, (pat != "." or shape in PRECONDITIONS)
    return DEFAULT_SHAPE.get(answer_type, "hop_aggregate"), False


# --------------------------------------------------------------------------- shape -> plan
def _works(scope="client", agg="sum_value", **kw):
    return {"source": "works", "scope": scope, "agg": agg, "filters": kw.pop("filters", {}), **kw}


def _inv(agg, scope="client"):
    return {"source": "invoices", "scope": scope, "agg": agg, "filters": {}}


def plan_for(shape, question, answer_type, facts):
    """Build the executable plan for a shape, reading its parameters out of the question."""
    raw = re.sub(r"\s+", " ", question).lower()
    nq = norm_q(question)
    P = lambda left, right=None, combine="left": {
        "anchor": {"client": None, "engineer": None, "work": None},
        "left": left, "right": right, "combine": combine, "note": f"rules:{shape}"}

    if shape == "hop_aggregate":
        return P(_works())
    if shape == "avg_work_size":
        return P(_works(agg="mean_value"))
    if shape == "work_count":
        return P(_works(agg="count"))
    if shape == "mean_minus_median":
        return P(_works(agg="mean_value"), _works(agg="median_value"), "difference")
    if shape == "rank_value":
        return P(_works(agg="nth_largest_value", n=1), _works(agg="nth_largest_value", n=2),
                 "difference")
    if shape == "top_n_clients":
        n = 3 if re.search(r"top (three|3)|(three|3) (largest|biggest)", raw) else 2
        return P(_works(scope="engineer", agg="top_n_sum_by_client", n=n))
    if shape == "largest_client_share":
        return P(_works(scope="engineer", agg="max_client_value"),
                 _works(scope="engineer", agg="sum_value"), "percent")
    if shape == "distinct_category":
        return P(_works(scope="engineer", agg="distinct_categories"))
    if shape == "distinct_clients":
        return P(_works(scope="engineer", agg="distinct_clients"))
    if shape == "business_units":
        return P(_works(agg="distinct_business_units"))
    if shape == "absence":
        return P(_works(agg="count", filters={"has_reference_letter": False}))
    if shape == "referenced_share":
        return P(_works(agg="count", filters={"has_reference_letter": True}),
                 _works(agg="count"), "percent")
    if shape == "collection_pct":
        return P(_inv("sum_received"), _inv("sum_invoiced"), "percent")
    if shape == "receivables_balance":
        return P(_inv("sum_invoiced"), _inv("sum_received"), "difference")
    if shape == "awarded_vs_invoiced":
        # Default is invoiced. Switch to received only on explicit collection language: an
        # unanchored "collect" also matches "recollection", and "claimed" means invoiced here.
        against = ("sum_received" if re.search(
            r"\b(collected|received|receipts?|realised|realized|cash received)\b", nq)
            else "sum_invoiced")
        return P(_works(), _inv(against), "abs_difference")
    if shape == "exclusion_aggregate":
        client = facts.resolve_client(question)
        cat = facts.resolve_category(question, client=client)
        if cat:
            return P(_works(filters={"categories_not_in": [cat]}))
        return P(_works(filters={"role": "Prime"}))          # "strip the subcontracted work"
    if shape == "category_pair_diff":
        client = facts.resolve_client(question)
        cats = facts.resolve_category_pair(question, client=client)
        if len(cats) >= 2:
            return P(_works(filters={"categories_in": [cats[0]]}),
                     _works(filters={"categories_in": [cats[1]]}), "abs_difference")
        return P(_works())
    if shape == "threshold_aggregate":
        bar = find_amount(norm_amt(question))
        return P(_works(filters={"min_value": bar} if bar else {}))
    if shape == "gap_to_threshold":
        bar = find_amount(norm_amt(question))
        if bar:
            return P({"source": "constant", "value": bar}, _works(), "difference")
        return P(_works())
    if shape == "role_split":
        role = ("JV Partner" if re.search(r"\bjv\b|joint venture|subcontract", nq) else "Prime")
        return P(_works(filters={"role": role}))
    if shape == "grading_filter":
        m = re.search(GRADES, raw)
        return P(_works(filters={"grading": m.group(0) if m else "satisfactory"}))
    if shape == "year_delta":
        ys = years_in(question)[:2]
        if len(ys) >= 2:
            return P(_works(filters={"years_in": [ys[0]]}),
                     _works(filters={"years_in": [ys[1]]}), "abs_difference")
        return P(_works())
    if shape == "temporal_chain":
        return P(_works(scope="engineer", filters={"completed_after": "ANCHOR_DATE"}))
    if shape == "date_span":
        return P(_works(scope="work", agg="days_since_anchor_date"))
    return P(_works())


def plan(question, answer_type, facts):
    """(plan, shape, matched). The plan is normalised, so the caller can execute it directly."""
    shape, matched = classify(question, answer_type, facts)
    raw = plan_for(shape, question, answer_type, facts)
    if re.search(CORPUS_SCOPE, re.sub(r"\s+", " ", question).lower()):
        for side in ("left", "right"):
            m = raw.get(side)
            if isinstance(m, dict) and m.get("scope") == "client":
                m["scope"] = "corpus"
    return dsl.normalise(raw), shape, matched
