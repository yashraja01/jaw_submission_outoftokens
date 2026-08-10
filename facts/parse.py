"""Parse the cached text into typed records with provenance.

Only the document types the 371 questions actually need are parsed:
  completion_certificate (155)  - the spine: value, dates, category, PM, grading
  past_performance_portfolio(1) - role, clean client name, corroborating value
  company_completion_cert (155) - independent cross-check
  reference_letter (132)        - presence/absence, corroborating role
  personnel_certificate (48)    - credential holder + issue date
  cv (39)                       - business unit
  Receivables_Ageing.xlsx       - invoiced / received per client
Bonds, dossiers, matrices, ISO, financials, ledgers, bank statements, RA bills, BOQ and the plant
register are extracted but not parsed: no question in questions.json references them.
"""
import datetime as dt
import json
import pathlib
import re
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent
TXT = ROOT / "build" / "txt"

flat = lambda t: re.sub(r"[ \t]+", " ", t.replace("\n", " ")).strip()
squeeze = lambda t: re.sub(r"\s+", " ", t).strip()
T = lambda d: (TXT / f"{d}.txt").read_text(encoding="utf-8")

# --------------------------------------------------------------------------- money
_NUM = r"\d[\d,]*(?:\.\d+)?"
_MONEY = re.compile(
    r"(?:INR|Rs\.?|₹)?\s*(" + _NUM + r")\s*(Crores?|Cr|Lakhs?|Lacs?|Mn|Million)?\b", re.I)


def parse_inr(s):
    """Every monetary string in the corpus goes through here. Decimal, never float."""
    if not s:
        return None
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
_MON = "January February March April May June July August September October November December".split()
_DATE = (r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2} [A-Z][a-z]{2,8} \d{4}"
         r"|[A-Z][a-z]{2,8} \d{1,2}, \d{4})")
_FMTS = ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y")


def parse_date(s):
    if not s:
        return None
    s = squeeze(s)
    for f in _FMTS:
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


# --------------------------------------------------------------------------- client names
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
    name = first[:m.start()] if m else first
    name = squeeze(name)
    # a wrapped name continues on the next line when that line has no subtitle marker
    if len(head) > 1 and not _SUBTITLE.search(head[1]) and not re.search(r"·|Certificate|CERTIFICATE", head[1]):
        nxt = squeeze(head[1])
        if nxt and len(nxt.split()) <= 3 and nxt[0].isupper():
            name = f"{name} {nxt}"
    return squeeze(name) or None


def norm_client(name):
    """Canonical key for a client organisation. Grouping is by NAME (frozen Phase 0 decision)."""
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


# --------------------------------------------------------------------------- completion certs
def parse_completion_certificate(doc_id):
    t = T(doc_id)
    f = flat(t)
    r = {"doc_id": doc_id, "src": {}}

    m = re.search(r"(?:Ref:|No\.)\s*(CC/\d+/\d+/\d+)", f)
    r["cert_ref"] = m.group(1) if m else None
    r["client_id"] = int(r["cert_ref"].split("/")[1]) if r["cert_ref"] else None
    r["client_header"] = client_from_header(t)
    m = re.search(r"·\s*([A-Za-z ]+?)\s*·\s*IN\b", f)
    r["state"] = squeeze(m.group(1)) if m else None

    def kv(label):
        m = re.search(r"^\s*" + label + r"\s{2,}(.+?)\s*$", t, re.M | re.I)
        return squeeze(m.group(1)) if m else None

    if kv("Name of Work"):
        r["family"] = "table"
        r["work_name"] = kv("Name of Work")
        r["category"] = kv(r"Nature / Category")
        r["value_raw"] = kv(r"Contract Value \(Original\)")
        r["completion_raw"] = kv("Completion Date")
        r["manager"] = kv(r"Contractor's Project Manager")
    else:
        r["family"] = "prose"
        m = re.search(r"work of\s*[“\"](.+?)[”\"]\s*\((.+?)\).*?completed in all respects on\s*"
                      + _DATE + r"\s*at a gross executed value of\s*(.+?)\s*\(Rupees", f)
        if m:
            r["work_name"], r["category"] = squeeze(m.group(1)), squeeze(m.group(2))
            r["completion_raw"], r["value_raw"] = m.group(3), squeeze(m.group(4))
        m = re.search(r"supervised on the contractor's side by\s+"
                      r"([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){0,3}?)\s*\.", f)
        r["manager"] = squeeze(m.group(1)) if m else None

    for k in ("work_name", "category", "value_raw", "completion_raw", "manager"):
        r.setdefault(k, None)

    m = re.search(r"graded\s+([A-Za-z ]+?)\s*[\.,]", f)
    r["grading"] = squeeze(m.group(1)) if m else None

    r["value"] = parse_inr(r["value_raw"])
    r["completed"] = parse_date(r["completion_raw"])
    m = re.search(r"Pkg-(\d+)", r["work_name"] or "")
    r["pkg"] = int(m.group(1)) if m else None
    r["src"] = {"value": r["value_raw"], "completed": r["completion_raw"],
                "manager": r["manager"], "grading": r["grading"]}
    return r


# --------------------------------------------------------------------------- portfolio
_PPP = re.compile(
    r"\n\s*(\d+)\.\s+(.+?)\n\s*Client\s{2,}(.+?)\s*\((Prime|JV Partner|Sub[-\s]?contractor)\)\s*\n"
    r"\s*Category\s{2,}(.+?)\n\s*Executed Value\s{2,}(.+?)\n"
    r"\s*Completed\s{2,}(.+?)\s*·\s*Certificate\s*(CC/\d+/\d+/\d+)", re.S)


def parse_portfolio():
    """Role + clean client name for all 155 works. Values here are 2dp-crore rounded — do not use."""
    raw = T("DOC-PPP-001")
    # role can wrap mid-phrase ("(JV\n Partner)"); flatten inside the Client cell only
    txt = re.sub(r"\(JV\s+Partner\)", "(JV Partner)", raw)
    txt = re.sub(r"\(Sub[-\s]*\n?\s*contractor\)", "(Sub-contractor)", txt)
    out = {}
    for _n, name, client, role, cat, val, comp, ref in _PPP.findall(txt):
        pkg = int(ref.split("/")[-1])
        out[pkg] = {"pkg": pkg, "cert_ref": ref, "client_name": squeeze(client),
                    "role": squeeze(role), "category": squeeze(cat).lower(),
                    "value_ppp": parse_inr(val), "work_name": squeeze(name),
                    "completed_ppp": parse_date(squeeze(comp))}
    return out


# --------------------------------------------------------------------------- company cert
def parse_company_certificate(doc_id):
    """Our own record of the same work. Two layout families; joined on Pkg-N, not on the ref."""
    t = T(doc_id)
    f = flat(t)
    g = lambda p: (lambda m: squeeze(m.group(1)) if m else None)(re.search(p, f, re.I))
    ref = g(r"Client Certificate Ref\s+(CC/\d+/\d+/\d+)")
    work = g(r"\bWork\s+(.+?)\s+Client\b") or g(r"Project Name\s+(.+?)\s+Client\b")
    value = (parse_inr(g(r"Executed Value\s+(.+?)\s+Completion"))
             or parse_inr(g(r"Contract Value\s+(.+?)\s+Completion")))
    manager = (g(r"Project Lead\s+(.+?)\s+Defect")
               or g(r"Project Manager\s+([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){0,3})"))
    m = re.search(r"Pkg-(\d+)", work or "")
    return {"doc_id": doc_id, "cert_ref": ref, "work_name": work,
            "client_name": g(r"\bClient\s+(.+?)\s*\((?:government|private|psu|public)"),
            "value": value, "manager": manager,
            "pkg": int(m.group(1)) if m else None}


# --------------------------------------------------------------------------- reference letters
_REF_PATS = [r"Project Name\s+(.+?)\s+Scope of Work",
             r"Work Executed\s+(.+?)\s+Value\s",
             r"Subject:.*?[“\"](.+?)[”\"]",
             r"for the work\s*[“\"](.+?)[”\"]",
             r"work\s*[“\"](.+?)[”\"]"]


def parse_reference_letter(doc_id):
    f = flat(T(doc_id))
    pkg = None
    for p in _REF_PATS:
        m = re.search(p, f)
        if m and "Pkg-" in m.group(1):
            pkg = int(re.search(r"Pkg-(\d+)", m.group(1)).group(1))
            break
    m = re.search(r"Contractor's Role\s+(Prime|JV Partner|Sub[-\s]?contractor)", f)
    return {"doc_id": doc_id, "pkg": pkg, "role": squeeze(m.group(1)) if m else None,
            "client_header": client_from_header(T(doc_id))}


# --------------------------------------------------------------------------- personnel certs
def parse_personnel_certificate(doc_id):
    f = flat(T(doc_id))
    r = {"doc_id": doc_id}
    m = re.search(r"This is to certify that\s+(.+?)\s+Employee ID:\s*(EMP-\d+)", f)
    if m:  # family A
        r["holder"], r["employee_id"] = squeeze(m.group(1)), m.group(2)
        ty = re.search(r"Credential Type\s+(.+?)\s+Credential ID\s+(\S+)", f)
        iss = re.search(r"Date of Issue\s+" + _DATE, f)
        r["credential"] = squeeze(ty.group(1)) if ty else None
        r["credential_id"] = ty.group(2) if ty else None
        r["issued"] = parse_date(iss.group(1)) if iss else None
    else:  # family B
        m = re.search(r"conferred upon\s+(.+?)\s+of National Infrastructure", f)
        r["holder"] = squeeze(m.group(1)) if m else None
        r["employee_id"] = None
        ty = re.search(r"Certification (?:Body|Authority)\s+(.+?)\s+This credential", f)
        r["credential"] = squeeze(ty.group(1)) if ty else None
        cn = re.search(r"Certificate No\.\s+(\S+)", f)
        r["credential_id"] = cn.group(1) if cn else None
        iss = re.search(r"Issued\s+" + _DATE, f)
        r["issued"] = parse_date(iss.group(1)) if iss else None
    return r


# --------------------------------------------------------------------------- CVs
def parse_cv(doc_id):
    f = flat(T(doc_id))
    g = lambda p: (lambda m: squeeze(m.group(1)) if m else None)(re.search(p, f))
    return {"doc_id": doc_id,
            "name": g(r"\bName\s+(.+?)\s+Employee ID\b"),
            "employee_id": g(r"Employee ID\s+(EMP-\d+)"),
            "designation": g(r"Designation\s+(.+?)\s+Business Unit\b"),
            "business_unit": g(r"Business Unit\s+(.+?)\s+Total Experience\b"),
            "experience": g(r"Total Experience\s+(\d+)\s*years"),
            "joined": parse_date(g(r"Date of Joining\s+" + _DATE) or "")}


# --------------------------------------------------------------------------- ageing workbook
def parse_receivables():
    books = json.load(open(ROOT / "build" / "workbooks.json"))
    sheet = books["Receivables_Ageing.xlsx"]["AR Ageing"]
    rows = sheet["rows"]
    head = [str(h).strip() for h in rows[0]]
    ix = {h: i for i, h in enumerate(head)}
    out = []
    for row in rows[1:]:
        if not row or row[ix["Client"]] in (None, "None", ""):
            continue
        d = lambda k: Decimal(str(row[ix[k]] or 0))
        out.append({"invoice": row[ix["Invoice No"]], "client_name": squeeze(str(row[ix["Client"]])),
                    "date": parse_date(str(row[ix["Invoice Date"]])[:10]),
                    "invoiced": d("Invoiced (INR)"), "received": d("Received (INR)"),
                    "outstanding": d("Outstanding (INR)"),
                    "status": row[ix["Status"]]})
    return out


def doc_ids(prefix):
    return sorted((p.stem for p in TXT.glob(f"{prefix}-*.txt")),
                  key=lambda s: (len(s), s))
