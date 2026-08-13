"""Typed records out of the cached text, with provenance.

Every field is extracted independently, by a list of alternative patterns, rather than by one
monolithic regex per document. The prose completion certificate used to be read by a single
pattern spanning work name, category, date and value; when any one of those four drifted, the whole
document was dropped and its work vanished from the corpus. Independent fields degrade one field at
a time instead.

Money is read by `parse_inr` on `Decimal` — never float. The corpus writes the same amount as
`INR 33.38 Cr`, `3,338.00 Lakh` and `33,38,00,000`, and the tolerance we are scored against is
0.5%, so a binary-float rounding on a ten-digit rupee figure is not something to leave to chance.
"""
import datetime as dt
import pathlib
import re
from decimal import Decimal

flat = lambda t: re.sub(r"[ \t]+", " ", t.replace("\n", " ")).strip()
squeeze = lambda t: re.sub(r"\s+", " ", t).strip()


class Corpus:
    """The ingested estate: text by doc id, doc ids by content-assigned type."""

    def __init__(self, build_dir):
        import json
        self.build = pathlib.Path(build_dir)
        self.txt = self.build / "txt"
        self.catalog = json.loads((self.build / "catalog.json").read_text(encoding="utf-8"))
        self.by_type = {}
        for r in self.catalog:
            self.by_type.setdefault(r["doc_type"], []).append(r["doc_id"])
        for v in self.by_type.values():
            v.sort()
        self._cache = {}

    def text(self, doc_id):
        if doc_id not in self._cache:
            p = self.txt / f"{doc_id}.txt"
            self._cache[doc_id] = p.read_text(encoding="utf-8") if p.exists() else ""
        return self._cache[doc_id]

    def ids(self, doc_type):
        return self.by_type.get(doc_type, [])

    def sheets(self, doc_id):
        import json
        p = self.txt / f"{doc_id}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# --------------------------------------------------------------------------- money
_NUM = r"\d[\d,]*(?:\.\d+)?"
_MONEY = re.compile(
    r"(?:INR|Rs\.?|₹)?\s*(" + _NUM + r")\s*(Crores?|Cr|Lakhs?|Lacs?|Mn|Million)?\b", re.I)


def parse_inr(s):
    """Every monetary string in the corpus goes through here. Decimal, never float."""
    if s is None:
        return None
    if isinstance(s, (int, float, Decimal)):
        return Decimal(str(s))
    m = _MONEY.search(str(s))
    if not m:
        return None
    n = Decimal(m.group(1).replace(",", ""))
    u = (m.group(2) or "").lower()
    if u.startswith("cr"):
        n *= 10_000_000
    elif u.startswith(("lakh", "lac")):
        n *= 100_000
    elif u.startswith(("mn", "million")):
        n *= 1_000_000
    return n


# --------------------------------------------------------------------------- dates
_DATE = (r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2} [A-Z][a-z]{2,8} \d{4}"
         r"|[A-Z][a-z]{2,8} \d{1,2}, \d{4})")
_FMTS = ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y")


def parse_date(s):
    if not s:
        return None
    if isinstance(s, (dt.date, dt.datetime)):
        return s.date() if isinstance(s, dt.datetime) else s
    s = squeeze(str(s))
    for f in _FMTS:
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    m = re.search(_DATE, s)
    if m and m.group(1) != s:
        return parse_date(m.group(1))
    return None


# --------------------------------------------------------------------------- names
_SUBTITLE = re.compile(
    r"\s{2,}(?:Government of India|Office of the|PSU CLIENT|government\b|Public Sector|"
    r"State Government|Municipal Body|Autonomous Body)|\s*·\s*[A-Z]", re.I)


def client_from_header(txt):
    """Issuer name from a certificate/letter header. Handles merge, wrap and caps variants."""
    lines = [l.rstrip() for l in txt.splitlines()]
    head = []
    for l in lines[:6]:
        if not l.strip():
            if head:
                break
            continue
        head.append(l)
        if len(head) >= 3:
            break
    if not head:
        return None
    first = head[0]
    m = _SUBTITLE.search(first)
    name = squeeze(first[:m.start()] if m else first)
    # a wrapped name continues on the next line when that line carries no subtitle marker
    if len(head) > 1 and not _SUBTITLE.search(head[1]) and not re.search(
            r"·|Certificate|CERTIFICATE", head[1]):
        nxt = squeeze(head[1])
        if nxt and len(nxt.split()) <= 3 and nxt[0].isupper():
            name = f"{name} {nxt}"
    return squeeze(name) or None


def norm_client(name):
    """Canonical key for a client organisation. Grouping is by name."""
    if not name:
        return None
    s = squeeze(str(name)).lower()
    s = s.replace("&", "and").replace(".", "").replace("'", "")
    s = re.sub(r"\bgovt\b", "government", s)
    s = re.sub(r"\bdept\b", "department", s)
    s = re.sub(r"\bcorp\b", "corporation", s)
    s = re.sub(r"\bltd\b", "limited", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return squeeze(s)


def norm_person(name):
    if not name:
        return None
    return squeeze(re.sub(r"[^A-Za-z ]+", " ", str(name))).lower()


def first(text, patterns, group=1, flags=re.I):
    """First pattern that matches, squeezed. Field-at-a-time extraction lives on this."""
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            try:
                v = m.group(group)
            except IndexError:
                v = m.group(0)
            if v and squeeze(v):
                return squeeze(v)
    return None


def pkg_of(s):
    m = re.search(r"Pkg[-\s]?(\d+)", str(s or ""), re.I)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- completion certs
def parse_completion_certificate(corpus, doc_id):
    t = corpus.text(doc_id)
    f = flat(t)
    r = {"doc_id": doc_id}

    r["cert_ref"] = first(f, [r"(?:Ref:|No\.|Reference:)\s*(CC/\d+/\d+/\d+)", r"\b(CC/\d+/\d+/\d+)"])
    r["client_id"] = int(r["cert_ref"].split("/")[1]) if r["cert_ref"] else None
    r["client_header"] = client_from_header(t)
    r["state"] = first(f, [r"·\s*([A-Za-z ]+?)\s*·\s*IN\b"])

    def kv(label):
        """A label/value row of the table family: two or more spaces separate the columns."""
        return first(t, [r"^[ \t]*" + label + r"\s{2,}(.+?)\s*$"], flags=re.M | re.I)

    if kv("Name of Work") or kv("Project Name"):
        r["family"] = "table"
        r["work_name"] = kv("Name of Work") or kv("Project Name")
        r["category"] = kv(r"Nature / Category") or kv("Category") or kv("Nature of Work")
        r["value_raw"] = (kv(r"Contract Value \(Original\)") or kv("Contract Value")
                          or kv("Executed Value") or kv("Value of Work"))
        r["completion_raw"] = kv("Completion Date") or kv("Date of Completion")
        r["manager"] = (kv(r"Contractor's Project Manager") or kv("Project Manager")
                        or kv("Project Lead"))
    else:
        r["family"] = "prose"
        r["work_name"] = first(f, [r"work of\s*[“\"](.+?)[”\"]", r"for the work\s*[“\"](.+?)[”\"]",
                                   r"[“\"]([^”\"]*Pkg-\d+)[”\"]"])
        r["category"] = first(f, [r"[”\"]\s*\((.+?)\)", r"\((.+?)\),?\s*awarded"])
        r["value_raw"] = first(f, [r"gross executed value of\s*(.+?)\s*\(Rupees",
                                   r"executed value of\s*(" + _NUM + r"[^,.]*?)[,.]",
                                   r"value of\s*(INR\s*" + _NUM + r"\s*\w*)"])
        r["completion_raw"] = first(f, [r"completed in all respects on\s*" + _DATE,
                                        r"completed on\s*" + _DATE,
                                        r"taken over on\s*" + _DATE])
        r["manager"] = first(
            f, [r"supervised on the contractor.s side by\s+([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+)"
                r"{0,3}?)\s*\.",
                r"under the supervision of\s+([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){0,3}?)\s*\.",
                r"Project Manager[:\s]+([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){0,3}?)\s*[.,]"])

    for k in ("work_name", "category", "value_raw", "completion_raw", "manager"):
        r.setdefault(k, None)
    r["grading"] = first(f, [r"graded\s+([A-Za-z ]+?)\s*[\.,]"])
    r["value"] = parse_inr(r["value_raw"])
    r["completed"] = parse_date(r["completion_raw"])
    r["pkg"] = pkg_of(r["work_name"]) or (int(r["cert_ref"].split("/")[-1]) if r["cert_ref"]
                                          else None)
    r["category"] = (r["category"] or "").lower() or None
    return r


def parse_company_certificate(corpus, doc_id):
    """Our own record of the same work. Two layout families; joined on the package number."""
    f = flat(corpus.text(doc_id))
    work = first(f, [r"\bWork\s+(.+?)\s+Client\b", r"Project Name\s+(.+?)\s+Client\b",
                     r"Name of Work\s+(.+?)\s+(?:Client|Category)\b"])
    return {
        "doc_id": doc_id,
        "cert_ref": first(f, [r"Client Certificate Ref\s+(CC/\d+/\d+/\d+)", r"\b(CC/\d+/\d+/\d+)"]),
        "work_name": work,
        "client_name": first(f, [r"\bClient\s+(.+?)\s*\((?:government|private|psu|public)"]),
        "value": parse_inr(first(f, [r"Executed Value\s+(.+?)\s+Completion",
                                     r"Contract Value\s+(.+?)\s+Completion",
                                     r"Executed Value\s+(INR[^A-Z]+)"])),
        "manager": first(f, [r"Project Lead\s+(.+?)\s+Defect",
                             r"Project Manager\s+([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){0,3})"]),
        "pkg": pkg_of(work),
    }


# --------------------------------------------------------------------------- portfolio
_PPP = re.compile(
    r"\n\s*(\d+)\.\s+(.+?)\n\s*Client\s{2,}(.+?)\s*\((Prime|JV Partner|Sub[-\s]?contractor)\)\s*\n"
    r"\s*Category\s{2,}(.+?)\n\s*Executed Value\s{2,}(.+?)\n"
    r"\s*Completed\s{2,}(.+?)\s*·\s*Certificate\s*(CC/\d+/\d+/\d+)", re.S)


def parse_portfolio(corpus):
    """Role and clean client name per work. Values here are 2dp-crore rounded — do not use them."""
    out = {}
    for doc_id in corpus.ids("past_performance_portfolio"):
        raw = corpus.text(doc_id)
        # a role can wrap mid-phrase in the extracted text
        txt = re.sub(r"\(JV\s+Partner\)", "(JV Partner)", raw)
        txt = re.sub(r"\(Sub[-\s]*\n?\s*contractor\)", "(Sub-contractor)", txt)
        for _n, name, client, role, cat, val, comp, ref in _PPP.findall(txt):
            pkg = pkg_of(name) or int(ref.split("/")[-1])
            out[pkg] = {"pkg": pkg, "cert_ref": ref, "client_name": squeeze(client),
                        "role": squeeze(role), "category": squeeze(cat).lower(),
                        "value_ppp": parse_inr(val), "work_name": squeeze(name),
                        "completed_ppp": parse_date(squeeze(comp))}
    return out


# --------------------------------------------------------------------------- reference letters
_REF_PATS = [r"Project Name\s+(.+?)\s+Scope of Work",
             r"Work Executed\s+(.+?)\s+Value\s",
             r"Subject:.*?[“\"](.+?)[”\"]",
             r"for the work\s*[“\"](.+?)[”\"]",
             r"work\s*[“\"](.+?)[”\"]"]


def parse_reference_letter(corpus, doc_id):
    t = corpus.text(doc_id)
    f = flat(t)
    pkg = None
    for p in _REF_PATS:
        m = re.search(p, f)
        if m and pkg_of(m.group(1)):
            pkg = pkg_of(m.group(1))
            break
    if pkg is None:                      # last resort: any package number anywhere in the letter
        pkg = pkg_of(f)
    return {"doc_id": doc_id, "pkg": pkg,
            "role": first(f, [r"Contractor's Role\s+(Prime|JV Partner|Sub[-\s]?contractor)"]),
            "client_header": client_from_header(t)}


# --------------------------------------------------------------------------- people
def parse_personnel_certificate(corpus, doc_id):
    f = flat(corpus.text(doc_id))
    r = {"doc_id": doc_id}
    m = re.search(r"This is to certify that\s+(.+?)\s+Employee ID:\s*(EMP-\d+)", f, re.I)
    if m:                                                        # family A
        r["holder"], r["employee_id"] = squeeze(m.group(1)), m.group(2)
        ty = re.search(r"Credential Type\s+(.+?)\s+Credential ID\s+(\S+)", f, re.I)
        r["credential"] = squeeze(ty.group(1)) if ty else None
        r["credential_id"] = ty.group(2) if ty else None
        r["issued"] = parse_date(first(f, [r"Date of Issue\s+" + _DATE]))
    else:                                                        # family B
        r["holder"] = first(f, [r"conferred upon\s+(.+?)\s+of\s+[A-Z]",
                                r"awarded to\s+(.+?)\s+of\s+[A-Z]"])
        r["employee_id"] = first(f, [r"(EMP-\d+)"])
        r["credential"] = first(f, [r"Certification (?:Body|Authority)\s+(.+?)\s+This credential",
                                    r"^\s*([A-Z][A-Za-z0-9 ]+?)\s+CERTIFICATION\b"])
        r["credential_id"] = first(f, [r"Certificate No\.\s+(\S+)", r"Credential ID:?\s*(\S+)"])
        r["issued"] = parse_date(first(f, [r"Issued\s+" + _DATE, r"Date of Issue\s+" + _DATE]))
    if not r.get("credential"):
        r["credential"] = first(f, [r"\b(PMP|Six Sigma Black Belt|Six Sigma Green Belt|PRINCE2|"
                                    r"Lean Six Sigma|ISO Lead Auditor)\b"])
    return r


def parse_cv(corpus, doc_id):
    f = flat(corpus.text(doc_id))
    return {"doc_id": doc_id,
            "name": first(f, [r"\bName\s+(.+?)\s+Employee ID\b"]),
            "employee_id": first(f, [r"Employee ID\s+(EMP-\d+)"]),
            "designation": first(f, [r"Designation\s+(.+?)\s+Business Unit\b"]),
            "business_unit": first(f, [r"Business Unit\s+(.+?)\s+Total Experience\b",
                                       r"Business Unit\s+(.+?)\s+(?:Designation|Date)\b"]),
            "experience": first(f, [r"Total Experience\s+(\d+)\s*years"]),
            "joined": parse_date(first(f, [r"Date of Joining\s+" + _DATE]))}


# --------------------------------------------------------------------------- bonds and bids
def parse_performance_bond(corpus, doc_id):
    f = flat(corpus.text(doc_id))
    return {"doc_id": doc_id,
            "bond_no": first(f, [r"Bond No:?\s*(\S+)", r"BG No:?\s*(\S+)"]),
            "issued": parse_date(first(f, [r"(?:Issue Date|Date):?\s*" + _DATE])),
            "tender_ref": first(f, [r"(RFP-\d+)"]),
            "amount": parse_inr(first(f, [r"amount not exceeding\s*(.+?)\s*\(Rupees",
                                          r"(?:bond|guarantee) amount[^0-9]{0,20}"
                                          r"((?:INR|Rs\.?)\s*" + _NUM + r"\s*\w*)",
                                          r"for a sum of\s*(.+?)\s*\(Rupees"])),
            "work_desc": first(f, [r"for the work of\s+(.+?),? and",
                                   r"Subject:\s*(?:Performance Bond|Performance Bank Guarantee)"
                                   r"\s*[—-]\s*(.+?)\s*\(Tender"], group=1),
            "bank": client_from_header(corpus.text(doc_id))}


def parse_tender_dossier(corpus, doc_id):
    f = flat(corpus.text(doc_id))
    return {"doc_id": doc_id,
            "tender_ref": first(f, [r"(RFP-\d+)"]),
            "bid_value": parse_inr(first(f, [r"Bid value:\s*(.+?)\s+Submitted",
                                             r"Our offer is\s*(.+?),"])),
            "submitted": parse_date(first(f, [r"Submitted:\s*" + _DATE])),
            "client": first(f, [r"The Tender Inviting Authority,\s*(.+?)\s+Dear"])}


# --------------------------------------------------------------------------- workbooks
def _header_index(row):
    return {squeeze(str(c)).lower(): i for i, c in enumerate(row) if c is not None}


def _col(ix, *names):
    """Column position by header name, matched loosely — headers are stable, not identical."""
    for n in names:
        for h, i in ix.items():
            if h == n or h.startswith(n) or n in h:
                return i
    return None


def parse_receivables(corpus):
    """Invoice-level receivables out of the ageing workbook.

    The workbook is found by its column headers, not its filename, and the columns by loose header
    match. A book titled `AR_Ageing_FY26.xlsx` with a `Received (INR)` column parses the same.
    """
    out = []
    for doc_id in corpus.ids("ageing_workbook"):
        for sheet_name, sheet in corpus.sheets(doc_id).items():
            rows = sheet.get("rows") or []
            if not rows:
                continue
            ix = _header_index(rows[0])
            c_client = _col(ix, "client", "customer", "party")
            c_inv = _col(ix, "invoiced", "invoice amount", "billed")
            c_rec = _col(ix, "received", "collected", "receipt")
            if c_client is None or c_inv is None:
                continue
            c_no = _col(ix, "invoice no", "invoice number", "document")
            c_date = _col(ix, "invoice date", "date")
            c_out = _col(ix, "outstanding", "balance", "due")
            c_status = _col(ix, "status")
            get = lambda row, i: row[i] if (i is not None and i < len(row)) else None
            for row in rows[1:]:
                if not row or get(row, c_client) in (None, "", "None"):
                    continue
                inv = parse_inr(get(row, c_inv)) or Decimal(0)
                rec = parse_inr(get(row, c_rec)) or Decimal(0)
                outstanding = parse_inr(get(row, c_out))
                out.append({
                    "doc_id": doc_id, "sheet": sheet_name,
                    "invoice": get(row, c_no), "client_name": squeeze(str(get(row, c_client))),
                    "date": parse_date(str(get(row, c_date))[:10] if get(row, c_date) else None),
                    "invoiced": inv, "received": rec,
                    "outstanding": outstanding if outstanding is not None else inv - rec,
                    "status": get(row, c_status)})
    return out


def parse_tables(corpus, doc_type):
    """Any other workbook, kept as rows so corpus-wide questions can still reach it."""
    out = []
    for doc_id in corpus.ids(doc_type):
        for sheet_name, sheet in corpus.sheets(doc_id).items():
            rows = sheet.get("rows") or []
            if len(rows) < 2:
                continue
            out.append({"doc_id": doc_id, "sheet": sheet_name,
                        "header": [squeeze(str(c)) if c is not None else "" for c in rows[0]],
                        "rows": rows[1:]})
    return out
