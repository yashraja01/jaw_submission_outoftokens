"""Work out which client, engineer, work and certificate a question is pointing at.

This is the half of the problem that does not look like arithmetic and decides most of the score:
correct arithmetic over the wrong client is indistinguishable from a right answer until it is
marked. Resolution is done here once per question, against the corpus, and the result is handed to
whichever planner produced the plan.

The language model may supply *mentions* — the span of the question it believes names the client,
the engineer, the work. A mention is only ever a hint about where to look: it is scored against the
corpus by the same resolver as the raw question, and if it fails to identify anything the whole
question is scored instead. Free text from the model is never taken as an entity.
"""
import re

from pipeline.facts import norm_q

CRED_TYPES = [
    ("pmp", r"\bpmp\b|project management professional"),
    ("six sigma black belt", r"six sigma black|black belt|\b6s\b"),
    ("six sigma green belt", r"six sigma green|green belt"),
    ("prince2", r"prince ?2"),
    ("lean", r"\blean\b"),
    ("iso lead auditor", r"lead auditor"),
]


class Context:
    """The resolved entities of one question, plus how each was arrived at."""

    def __init__(self, facts, question, client=None, manager=None, work=None,
                 anchor_date=None, trace=None):
        self.facts = facts
        self.question = question
        self.client = client
        self.manager = manager
        self.work = work
        self.anchor_date = anchor_date
        self.trace = trace or {}

    def summary(self):
        return {"client": self.client["name"] if self.client else None,
                "engineer": self.manager["name"] if self.manager else None,
                "work": self.work["work_name"] if self.work else None,
                "anchor_date": str(self.anchor_date) if self.anchor_date else None,
                **self.trace}


def _resolve_with_mention(resolver, mention, question, kind, trace):
    """Try the model's mention first, then the whole question. Corpus decides either way."""
    if mention:
        hit = resolver(mention)
        if hit:
            trace[f"{kind}_from"] = "mention"
            return hit
        trace[f"{kind}_mention_missed"] = mention[:60]
    hit = resolver(question)
    if hit:
        trace.setdefault(f"{kind}_from", "question")
    return hit


def resolve_credential(facts, text, manager):
    """The certificate a question dates its comparison from.

    Preferred order: an explicit credential id quoted in the question, then the named engineer's
    certificate of the named type, then any certificate of that type. Questions quote ids
    ("Six Sigma Black Belt (6S-500161)"), and an id identifies one certificate exactly where a
    type-and-holder match can still be ambiguous.
    """
    low = re.sub(r"\s+", " ", text).lower()
    for c in facts.credentials:
        cid = (c.get("credential_id") or "").strip().lower()
        if cid and len(cid) >= 5 and cid in low:
            return c, "credential id"

    want = next((name for name, pat in CRED_TYPES if re.search(pat, low)), None)
    pool = facts.credentials
    if manager:
        mine = [c for c in pool if c["holder_key"] == manager["key"]]
        if mine:
            typed = [c for c in mine if want and want in (c["credential"] or "").lower()]
            hit = typed or mine
            if len(hit) == 1:
                return hit[0], "engineer's certificate"
            if hit:
                return min(hit, key=lambda c: str(c["issued"])), "engineer's earliest certificate"
    if want:
        typed = [c for c in pool if want in (c["credential"] or "").lower()]
        if typed:
            # no holder to pin it to: the modal issue date of that credential type
            dates = [str(c["issued"]) for c in typed]
            common = max(set(dates), key=dates.count)
            return next(c for c in typed if str(c["issued"]) == common), "credential type only"
    return None, None


def build(facts, question, mentions=None):
    """Resolve one question's anchors. `mentions` is the model's optional hint, or None."""
    mentions = mentions or {}
    text = question
    trace = {}

    work = _resolve_with_mention(
        lambda s: facts.resolve_work(s), mentions.get("work"), text, "work", trace)
    manager = _resolve_with_mention(
        lambda s: facts.resolve_manager(s, hint_work=work), mentions.get("engineer"), text,
        "engineer", trace)

    if work is None and manager:
        work = facts.resolve_work(text, manager=manager)      # oblique work, engineer-constrained
    nq = norm_q(text)
    explicit_pkg = re.search(r"\b(?:pkg|package)\s*\d+", nq) is not None
    if (work is not None and manager and work["manager_key"] != manager["key"]
            and not explicit_pkg):
        # an oblique work reference ("the west bengal hospital block") must be one the named
        # engineer actually led, not the first corpus-wide name match
        work = facts.resolve_work(text, manager=manager) or work

    def client_resolver(s):
        return facts.resolve_client(s, mask_work=work if explicit_pkg else None)

    client = _resolve_with_mention(client_resolver, mentions.get("client"), text, "client", trace)
    if client is None:
        # A client named without its state — "the Public Works Department account" — scores the
        # same against five departments and so resolves to none of them. The categories the
        # question goes on to name break the tie: usually only one of the candidates has works in
        # both of them.
        cats = facts.resolve_category_pair(text)
        if cats:
            client = facts.resolve_client(text, must_have_categories=cats)
            if client:
                trace["client_from"] = "the categories named"
    if client is None:
        client = facts.client_of_work(work)
        if client:
            trace["client_from"] = "the named work"
    if client is None and manager:
        client = facts.primary_client_of(manager)
        if client:
            trace["client_from"] = "the engineer's principal account"

    cred, how = resolve_credential(facts, text, manager)
    anchor_date = None
    m = re.search(r"\b(20\d\d-\d\d-\d\d)\b", text)
    if m:                                   # a date written in the question outranks a lookup
        anchor_date, trace["anchor_date_from"] = m.group(1), "stated in the question"
    elif cred:
        anchor_date, trace["anchor_date_from"] = cred["issued"], how

    return Context(facts, question, client=client, manager=manager, work=work,
                   anchor_date=anchor_date, trace=trace)
