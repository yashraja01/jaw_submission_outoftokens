"""Classify each question into a shape and extract its parameters.

Shape misclassification is the dominant scoring loss under the continuous scorer (a hop_aggregate
read as an avg_work_size on a 9-work client scores ~0.11), so rules are ordered specific-first and
every shape is constrained to its declared answer_type.
"""
import re

from qa.resolve import norm_q

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}


def words_to_number(s):
    toks = re.split(r"[\s-]+", s.strip().lower())
    total, cur = 0, 0
    seen = False
    for t in toks:
        if t in UNITS:
            cur += UNITS[t]; seen = True
        elif t in TENS:
            cur += TENS[t]; seen = True
        elif t == "hundred" and seen:
            cur *= 100
        else:
            return None
    return total + cur if seen else None


def norm_amt(text):
    """Lowercase but keep decimal points: norm_q() strips them, turning '23.0 Cr' into '23 0 cr'
    and making the amount parse as 0."""
    s = re.sub(r"[’‘]", "'", str(text)).lower()
    s = s.replace(",", "").replace("&", " and ")
    s = re.sub(r"[^a-z0-9. -]+", " ", s)
    return re.sub(r"\s+", " ", s)


def find_amount(nq):
    """A rupee threshold stated in words or digits: 'twenty-six crore', '70cr', 'INR 20 Cr'.

    Number words are read by walking back from each unit token and trimming from the left until the
    remainder parses — a leftmost regex match would otherwise swallow 'contracts hitting the six'.
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


GRADES = r"satisfactory|very good|excellent|marked good|graded good|came back as good"
# \b prefixes matter: an unanchored "collect" also matches "recollection", which appears constantly
BILLED = (r"\binvoic|\bbilled\b|\bbills\b|bill so far|submitted claims|claims we submitted|"
          r"\bcollect(?:ed|ion|ing)?\b|\breceived\b|\breceipts?\b|\boutstanding\b|\bunbilled\b|"
          r"cash flow|\brealis|\brealiz|commitments and our bills|against the claims")
MEANMED = (r"(mean|average|avg)[^.?]{0,60}\bmedian\b|\bmedian\b[^.?]{0,60}(mean|average|avg)|"
           r"mean-median|avg minus median")

RULES = [
    ("date_span",            "days",    r"."),

    ("collection_pct",       "percent", r"collect|receipt|cleared|actually (been )?(received|brought in)|"
                                        r"portion of the total billed|against the total billed|on billed|"
                                        r"amounts billed|total billed"),
    ("referenced_share",     "percent", r"reference|testimonial|sign-?off|recommendation|approval|"
                                        r"verification|endorse|backed by"),
    ("largest_client_share", "percent", r"largest|biggest|top (client|account)|primary account|"
                                        r"single account|main client|top account|foremost|primary client"),

    # \black\b, not bare "lack": every Six Sigma question contains the word "Black"
    ("absence",              "count",   r"\black\b|\blacking\b|no (client )?reference|without a|"
                                        r"missing|absent|unreferenced|do not have a|don't have a"),
    ("business_units",       "count",   r"business unit|internal (unit|team|division)|"
                                        r"separate (internal )?(unit|division)|distinct internal|"
                                        r"internal teams|separate units"),
    ("pair_overlap",         "count",   r"\bboth\b|jointly|overlap|in common|"
                                        r"\w+ and \w+ (covered|handled|served|logged|delivered)|"
                                        r"covered .{0,40}(account|under)"),
    ("distinct_category",    "count",   r"categor|classification|different (kinds|types)|work type|"
                                        r"types of work|distinct work"),
    ("work_count",           "count",   r"."),

    ("mean_minus_median",    "money",   MEANMED),
    ("grading_filter",       "money",   GRADES),
    ("year_delta",           "money",   r"(19|20)\d\d\D{1,30}(19|20)\d\d"),
    # before awarded_vs_invoiced: "outstanding contract value we still need to secure ... to clear
    # the 120 Cr credential target" is a gap-to-target, not a billing gap
    ("gap_to_threshold",     "money",   r"how much (more|additional|further)|need to (clear|reach|secure)|"
                                        r"still need to secure|shortfall (to|against)|credential target|"
                                        r"to reach our|more value do we need|to clear the"),
    ("awarded_vs_invoiced",  "money",   BILLED),
    ("top_n_clients",        "money",   r"(two|three|2|3) (largest|biggest)|top (two|three|2|3)|"
                                        r"two (biggest|largest) client|top accounts|"
                                        r"(largest|biggest) two client|two client (relationships|"
                                        r"engagements)|his two clients|her two clients"),
    # "exceed" alone also matches "meeting or exceeding <amount>", which is a threshold question
    ("rank_value",           "money",   r"exceeds? (the )?(next|second)|beats|next one down|"
                                        r"just behind|second[- ]largest|"
                                        r"next largest|biggest and next|one down|the subsequent one|"
                                        r"difference between (the |our )?(largest|biggest|highest)|"
                                        r"gap between (the )?(largest|top|biggest|highest)"),
    ("exclusion_aggregate",  "money",   r"excluding|excluded|exclude|except|strip|leaving out|"
                                        r"other than|net of|remove the|removing the|without the|"
                                        r"less the|minus the|filter out|set aside|once we remove|"
                                        r"dropping the|after dropping|drop the"),
    ("gap_to_threshold",     "money",   r"how much (more|additional|further)|need to (clear|reach|secure|hit)|"
                                        r"shortfall (to|against)|credential target|to reach our|"
                                        r"more value do we need"),
    ("threshold_aggregate",  "money",   r"crore (mark|line|threshold|bar|cutoff|limit)|clearing the|"
                                        r"crossing the|hitting the|above inr|over inr|exceeding|"
                                        r"at or above|heavier|or more\b|or higher\b|clear that mark|"
                                        r"meeting the|cutoff|\blimit\b|that clear|valued at"),
    ("role_split",           "money",   r"as prime|prime capacity|\bprime\b|jv partner|joint venture|"
                                        r"as a jv|\bjv\b|subcontractor allocation|non-billable phases"),
    ("avg_work_size",        "money",   r"average|mean\b|typical (size|scale)|avg|per-project size|"
                                        r"typical (project|contract)|defensible average"),
    ("temporal_chain",       "money",   r"after (that|the|his|her|this) (date|march|issue|certification|"
                                        r"pmp)|finished after|completed after|wrapped up after|"
                                        r"reached completion after|post[- ]cert|since (that|his|her)"),
    ("hop_aggregate",        "money",   r"."),
]


def _years(text):
    ys = {int(y) for y in re.findall(r"\b(19\d\d|20[0-2]\d)\b", text) if 2009 <= int(y) <= 2026}
    return sorted(ys)


# Some shapes are only valid if their parameter is actually present. Without this, filler like
# "before the bid cutoff" routes a temporal_chain into threshold_aggregate.
PREDICATES = {
    "threshold_aggregate": lambda s, raw: find_amount(norm_amt(raw)) is not None,
    "gap_to_threshold": lambda s, raw: find_amount(norm_amt(raw)) is not None,
    "year_delta": lambda s, raw: len(_years(raw)) >= 2,
}


def classify(q):
    t, s = q["answer_type"], norm_q(q["question"])
    raw = re.sub(r"\s+", " ", q["question"]).lower()
    for shape, at, pat in RULES:
        if at != t or not (re.search(pat, s) or re.search(pat, raw)):
            continue
        pred = PREDICATES.get(shape)
        if pred and not pred(s, raw):
            continue
        return shape
    return "hop_aggregate"


def parameters(q, shape, facts):
    """Shape-specific arguments, resolved against the entity tables."""
    text = q["question"]
    nq = norm_q(text)
    p = {}
    work = facts.resolve_work(text)
    p["manager"] = facts.resolve_manager(text, hint_work=work)
    if work is None and p["manager"]:
        work = facts.resolve_work(text, manager=p["manager"])   # oblique work, engineer-constrained
    if (work is not None and p["manager"] and work["manager_key"] != p["manager"]["key"]
            and not re.search(r"\b(?:pkg|package)\s*\d+", nq)):
        # an oblique work reference ("the west bengal hospital block") must be one the named
        # engineer actually led, not the first corpus-wide name match
        work = facts.resolve_work(text, manager=p["manager"]) or work
    p["work"] = work
    p["client"] = facts.resolve_client(text) or facts.client_of_work(work)
    if p["client"] is None and p["manager"]:
        # "the client linked to X's portfolio", "X's principal account", "her top client"
        p["client"] = facts.primary_client_of(p["manager"])
        p["client_from"] = "manager's largest client"
    p["managers"] = facts.resolve_managers_all(text)

    if shape in ("exclusion_aggregate",):
        p["category"] = facts.resolve_category(text, client=p["client"])
    if shape in ("threshold_aggregate", "gap_to_threshold"):
        p["amount"] = find_amount(norm_amt(text))
    if shape == "year_delta":
        p["years"] = _years(text)[:2]
    if shape == "role_split":
        p["role"] = ("JV Partner" if re.search(r"jv|joint venture|subcontractor allocation", nq)
                     else "Prime")
    if shape == "grading_filter":
        m = re.search(GRADES, nq)
        g = m.group(0) if m else "satisfactory"
        p["grading"] = {"marked good": "good", "graded good": "good",
                        "came back as good": "good"}.get(g, g)
    if shape == "awarded_vs_invoiced":
        # two sub-variants: awarded vs invoiced, and awarded vs actually collected
        p["against"] = ("received" if re.search(
            r"collect|received|receipt|cash flow|realis|realiz", nq) else "invoiced")
    if shape == "date_span":
        p["issued"] = facts.credential_issue_date(text)
        m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", text)
        if m:
            p["issued"] = m.group(0)
    return p
