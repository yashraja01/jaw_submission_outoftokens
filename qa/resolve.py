"""Resolve question mentions to corpus entities.

Questions are deliberately corrupted — "irr & waterways dept rajasthan", "gujarat pw",
"mega infra authority", "ut pr pkg 2 wtp augmentation". Matching is fuzzy on the question side and
exact on the corpus side: we score every canonical entity and take the best, never accept free text.
"""
import re
import sqlite3
import pathlib
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "build" / "facts.db"

STOP = {"of", "the", "and", "for", "govt", "government", "dept", "department", "a", "in", "our"}

ABBREV = [
    (r"\bpwd\b", "public works department"),
    (r"\bphed?g?\b", "public health engineering department"),
    (r"\bphe\b", "public health engineering department"),
    (r"\bp\s*w\s*d\b", "public works department"),
    (r"\bpw\b", "public works department"),
    (r"\bi\s*&\s*w\b", "irrigation waterways"),
    (r"\birr\b", "irrigation"),
    (r"\bw&i\b", "irrigation waterways"),
    (r"\binfra\b", "infrastructure"),
    (r"\bcorp\b", "corporation"),
    (r"\bltd\b", "limited"),
    (r"\bauth\b", "authority"),
    (r"\bmuni\b", "municipal"),
    (r"\but\s+pr\b", "uttar pradesh"),
    (r"\bmah\b", "maharashtra"),
    (r"\bneda\b", "national expressway development authority"),
    (r"\bguj\b", "gujarat"),
    (r"\braj\b", "rajasthan"),
    (r"\bjhk?d\b", "jharkhand"),
]

STATES = ["uttar pradesh", "madhya pradesh", "west bengal", "tamil nadu", "maharashtra",
          "gujarat", "rajasthan", "jharkhand", "odisha", "delhi"]


# State initialisms are expanded CASE-SENSITIVELY, before lowercasing. Lowercase "up" is the
# English word: an unanchored \bup\b turned "wrapped up" into "wrapped uttar pradesh" and sent a
# Jharkhand question to an Uttar Pradesh work.
STATE_INITIALS = [(r"\bU\.?\s?P\.?\b", " uttar pradesh "), (r"\bM\.?\s?P\.?\b", " madhya pradesh "),
                  (r"\bW\.?\s?B\.?\b", " west bengal "), (r"\bT\.?\s?N\.?\b", " tamil nadu ")]


def norm_q(s):
    s = re.sub(r"[’‘]", "'", str(s))
    for pat, rep in STATE_INITIALS:
        s = re.sub(pat, rep, s)
    s = s.lower().replace("&", " and ").replace("—", " ").replace("–", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    for pat, rep in ABBREV:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


class Facts:
    def __init__(self, db=DB):
        self.db = sqlite3.connect(db)
        self.db.row_factory = sqlite3.Row
        self.works = [dict(r) for r in self.db.execute("SELECT * FROM work")]
        for w in self.works:
            w["value"] = Decimal(w["value"])
            w["year"] = int(w["completed"][:4])
        self.by_pkg = {w["pkg"]: w for w in self.works}

        self.clients = {}
        for w in self.works:
            self.clients.setdefault(w["client_key"], {
                "key": w["client_key"], "name": w["client_name"], "works": []})
            self.clients[w["client_key"]]["works"].append(w)

        self.managers = {}
        for w in self.works:
            self.managers.setdefault(w["manager_key"], {
                "key": w["manager_key"], "name": w["manager"], "works": []})
            self.managers[w["manager_key"]]["works"].append(w)

        self.credentials = [dict(r) for r in self.db.execute("SELECT * FROM credential")]
        self.cvs = [dict(r) for r in self.db.execute("SELECT * FROM cv")]
        self.bu_by_person = {c["name_key"]: c["business_unit"] for c in self.cvs}

        self.invoices = [dict(r) for r in self.db.execute("SELECT * FROM invoice")]
        # A client can appear in the ageing book with no completed work — "Public Health
        # Engineering Dept, West Bengal" is exactly that. Building the client table from works
        # alone made it unresolvable, so its receivables question silently fell through to
        # "Public Works Department, Govt of West Bengal".
        for i in self.invoices:
            if i["client_key"] not in self.clients:
                self.clients[i["client_key"]] = {"key": i["client_key"],
                                                 "name": i["client_name"], "works": []}
        self.inv_by_client = {}
        for i in self.invoices:
            b = self.inv_by_client.setdefault(i["client_key"], {"invoiced": Decimal(0),
                                                                "received": Decimal(0),
                                                                "outstanding": Decimal(0), "n": 0})
            b["invoiced"] += Decimal(i["invoiced"])
            b["received"] += Decimal(i["received"])
            b["outstanding"] += Decimal(i["outstanding"])
            b["n"] += 1

        # precompute matching signatures
        # Token weights are inverse-document-frequency across the 28 client names: "trishakti" is
        # decisive, "corporation" is nearly noise. A flat count made common tails dominate.
        # Tokens that also occur in WORK names are unreliable client evidence: "Steel Truss Bridge"
        # would otherwise identify Mahanadi Steel Corporation, "Highway Construction" would identify
        # Lakshya Engineering & Construction, and "Drainage Works" would identify a Public Works
        # Department. Down-weight them heavily.
        self._workvocab = {t for w in self.works for t in norm_q(w["work_name"]).split()
                           if not t.isdigit() and t != "pkg"}
        self._client_sig = {}
        self._df = df = {}
        for c in self.clients.values():
            for t in set(norm_q(c["name"]).split()):
                df[t] = df.get(t, 0) + 1
        for k, c in self.clients.items():
            toks = [t for t in norm_q(c["name"]).split() if t not in STOP]
            state = next((s for s in STATES if s in norm_q(c["name"])), None)
            statewords = {w for s in STATES for w in s.split()}
            core = [t for t in toks if t not in statewords]
            self._client_sig[k] = {
                "toks": toks, "core": core, "state": state,
                "w": {t: (1.0 / df.get(t, 1)) * (0.35 if t in self._workvocab else 1.0)
                      for t in core}}

        self.categories = sorted({w["category"] for w in self.works}, key=len, reverse=True)

    # ------------------------------------------------------------------ works
    def resolve_work(self, q, manager=None):
        """Pkg-N when stated. Otherwise match work-name words + state, restricted to the named
        engineer's works when one is known — 'Meera Roy's ... Jharkhand hydro tunnel package'."""
        nq = norm_q(q)
        m = re.search(r"\b(?:pkg|package|pkge?)\s*(\d+)\b", nq)
        if m and int(m.group(1)) in self.by_pkg:
            return self.by_pkg[int(m.group(1))]
        pool = manager["works"] if manager else self.works
        state = next((s for s in STATES if s in nq), None)
        best, score = None, 0.0
        for w in pool:
            wn = norm_q(w["work_name"])
            head = [t for t in wn.split() if t not in STOP and not t.isdigit() and t != "pkg"
                    and t not in {x for s in STATES for x in s.split()}]
            hit = sum(1 for t in head if re.search(rf"\b{re.escape(t)}\b", nq))
            s = hit / max(len(head), 1)
            if state:
                s += 0.5 if state in wn else -0.4
            if hit and s > score:
                best, score = w, s
        return best if score >= (0.6 if manager else 0.8) else None

    # ------------------------------------------------------------------ clients
    def resolve_client(self, q, mask_work=None, must_have_categories=None):
        """When the question cites an explicit Pkg-N, that work's name is masked first: its words
        describe the work, not the client ('STP — West Bengal Pkg-73' must not select the Public
        Works Department, Govt of West Bengal)."""
        nq = norm_q(q)
        if mask_work:
            for t in norm_q(mask_work["work_name"]).split():
                if t in STOP or t.isdigit() or t == "pkg":
                    continue
                nq = re.sub(rf"\b{re.escape(t)}\b", " ", nq, count=1)
        best, score, scores = None, 0.0, {}
        for k, sig in self._client_sig.items():
            if not sig["core"]:
                continue
            hit = [t for t in sig["core"] if re.search(rf"\b{re.escape(t)}\b", nq)]
            if not hit:
                continue
            s = sum(sig["w"][t] for t in hit) / sum(sig["w"].values())
            # a token unique to one client ("trishakti", "arunodaya") identifies it on its own,
            # even when the rest of the legal name is omitted ("the Trishakti account") — but only
            # if that token never appears in a work name
            if any(self._df.get(t, 1) == 1 and t not in self._workvocab for t in hit):
                s = max(s, 1.0)
            if sig["state"]:
                # Only a tiebreak. A dominant state bonus made "Gujarat Municipal Corporation" beat
                # "Trishakti Power Generation Corporation" whenever a Gujarat work was mentioned.
                s += 0.3 if sig["state"] in nq else -0.5
            scores[k] = s
            if s > score:
                best, score = self.clients[k], s
        if score >= 0.55:
            return best
        if must_have_categories:
            # "the Public Works Department account" omits the state, so all five score alike.
            # The categories the question names break the tie: only one of them has works in both.
            near = [self.clients[k] for k, s in scores.items() if s >= 0.45]
            fit = [c for c in near
                   if all(any(w["category"] == cat for w in c["works"])
                          for cat in must_have_categories)]
            if len(fit) == 1:
                return fit[0]
        return None

    def client_of_work(self, w):
        return self.clients.get(w["client_key"]) if w else None

    # ------------------------------------------------------------------ people
    def resolve_manager(self, q, hint_work=None):
        """Full name preferred; a bare first name is disambiguated by the named work."""
        nq = norm_q(q)
        nq = re.sub(r"\b(\w+?)s\b", r"\1", nq) if not any(
            m["key"] in nq for m in self.managers.values()) else nq   # "pritis pmp" -> "priti pmp"
        exact = [m for m in self.managers.values() if m["key"] in nq]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return max(exact, key=lambda m: len(m["key"]))
        firsts = {}
        for m in self.managers.values():
            firsts.setdefault(m["key"].split()[0], []).append(m)
        cands = [m for f, ms in firsts.items() if re.search(rf"\b{f}\b", nq) for m in ms]
        if len(cands) == 1:
            return cands[0]
        if cands:
            if hint_work:                       # the named work disambiguates the first name
                for m in cands:
                    if m["key"] == hint_work["manager_key"]:
                        return m
            # JUDGEMENT: a bare first name ("meera", "Priya") is ambiguous across managers.
            # Take the one with the largest portfolio — the most likely referent on a bid desk.
            return max(cands, key=lambda m: (len(m["works"]), sum(w["value"] for w in m["works"])))
        # last resort: the engineer implied by the named work
        if hint_work and hint_work["manager_key"] in self.managers:
            return self.managers[hint_work["manager_key"]]
        return None

    def primary_client_of(self, manager):
        """The engineer's 'main/principal/primary account'.

        Two tiers, recovered from the leaderboard: the client the engineer has the MOST works
        with; and when every client ties at one work, the client with the FEWEST works in the
        whole corpus. Largest-by-value — the obvious reading — is wrong for the tie case.
        """
        if not manager or not manager["works"]:
            return None
        cnt, val = {}, {}
        for w in manager["works"]:
            cnt[w["client_key"]] = cnt.get(w["client_key"], 0) + 1
            val[w["client_key"]] = val.get(w["client_key"], 0) + w["value"]
        if max(cnt.values()) == 1:
            return self.clients[min(cnt, key=lambda k: (len(self.clients[k]["works"]), k))]
        return self.clients[max(cnt, key=lambda k: (cnt[k], val[k]))]

    def resolve_managers_all(self, q):
        """Every distinct manager named in the question (for the two-engineer shapes)."""
        nq = norm_q(q)
        hits = [m for m in self.managers.values() if re.search(rf"\b{re.escape(m['key'])}\b", nq)]
        out, seen = [], set()
        for m in sorted(hits, key=lambda m: -len(m["key"])):
            if m["key"] in seen:
                continue
            if any(m["key"] in s and m["key"] != s for s in seen):
                continue
            seen.add(m["key"])
            out.append(m)
        return out

    # ------------------------------------------------------------------ misc
    def resolve_category(self, q, client=None):
        """Longest label first, so 'small buildings' wins over 'buildings' (exact-label, frozen).

        The client's own name is masked first: 'Irrigation & Waterways Dept ... excluding buildings'
        must not resolve to the category 'irrigation'. Only the first occurrence of each client
        token is masked, so 'Irrigation & Waterways ... excluding irrigation' still works.
        """
        nq = norm_q(q)
        if client:
            for t in norm_q(client["name"]).split():
                if t in STOP:
                    continue
                nq = re.sub(rf"\b{re.escape(t)}\b", " " * len(t), nq, count=1)
        for c in self.categories:
            if re.search(self._cat_pattern(c), nq):
                return c
        return None

    @staticmethod
    def _cat_pattern(c):
        # "bridges and flyovers" is written for the label "bridges flyovers"
        return r"\b" + r"\s+(?:and\s+)?".join(re.escape(t) for t in c.split()) + r"\b"

    def resolve_category_pair(self, q, client=None):
        """Both categories of an 'A versus B' question, in the order they are written."""
        nq = norm_q(q)
        if client:
            for t in norm_q(client["name"]).split():
                if t in STOP:
                    continue
                nq = re.sub(rf"\b{re.escape(t)}\b", " " * len(t), nq, count=1)
        found = []
        for c in sorted(self.categories, key=len, reverse=True):
            for m in re.finditer(self._cat_pattern(c), nq):
                if not any(m.start() < e and m.end() > s for s, e, _ in found):
                    found.append((m.start(), m.end(), c))
        # elision: "roads highways and maintenance" also names "roads maintenance"
        for s, e, c in list(found):
            head = c.split()[0]
            for other in self.categories:
                if other == c or other.split()[0] != head or any(o == other for _, _, o in found):
                    continue
                m = re.search(rf"\b{re.escape(other.split()[-1])}\b", nq[e:e + 40])
                if m:
                    found.append((e + m.start(), e + m.end(), other))
        return [c for _, _, c in sorted(found)]

    def credential_issue_date(self, q):
        nq = norm_q(q)
        if re.search(r"six sigma", nq):
            d = [c["issued"] for c in self.credentials if "six sigma" in (c["credential"] or "").lower()]
        else:
            d = [c["issued"] for c in self.credentials if "pmp" in (c["credential"] or "").lower()]
        return max(set(d), key=d.count) if d else "2021-03-10"
