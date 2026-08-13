"""Join the parsed records into one fact store and persist it.

What changed from the earlier build: the integrity gate used to assert the *shipped* corpus — 155
works, Σ 55,303,999,999, 132 reference letters — and a mismatch aborted the run. Against a
different estate every one of those assertions is wrong by construction, so they are now
proportional consistency checks (does the company's own certificate agree with the client's? does
the portfolio agree with both?) plus a report of what was parsed. Only an empty store stops the
run.
"""
import sqlite3
import sys
from decimal import Decimal

from pipeline import parse as P


def build(corpus, log=print):
    store = {"issues": []}
    bad = store["issues"].append

    # ---------- works: the client's completion certificate is the spine ----------
    certs, dropped = {}, 0
    for d in corpus.ids("completion_certificate"):
        c = P.parse_completion_certificate(corpus, d)
        if c["pkg"] is None or c["value"] is None or c["completed"] is None:
            bad(f"completion certificate {d}: incomplete parse ({c['work_name']!r}, "
                f"value={c['value_raw']!r}, completed={c['completion_raw']!r})")
            dropped += 1
            continue
        if c["pkg"] in certs:
            bad(f"completion certificate {d}: package {c['pkg']} already claimed by "
                f"{certs[c['pkg']]['doc_id']}")
            continue
        certs[c["pkg"]] = c
    ppp = P.parse_portfolio(corpus)

    works = {}
    for pkg, c in certs.items():
        p = ppp.get(pkg, {})
        works[pkg] = {
            "pkg": pkg, "cert_doc": c["doc_id"], "cert_ref": c["cert_ref"],
            "client_id": c["client_id"],
            "client_name": p.get("client_name") or c["client_header"],
            "client_key": P.norm_client(p.get("client_name") or c["client_header"]),
            "state": c["state"], "work_name": c["work_name"],
            "category": (c["category"] or "").lower(),
            "value": c["value"],                       # the certificate is authoritative for money
            "completed": c["completed"],
            "manager": c["manager"], "manager_key": P.norm_person(c["manager"]),
            "grading": c["grading"], "family": c["family"],
            "role": p.get("role"), "has_reference_letter": False, "reference_docs": [],
        }
        if p and p.get("category") and p["category"] != works[pkg]["category"]:
            bad(f"pkg {pkg}: category {c['category']!r} (cert) vs {p['category']!r} (portfolio)")

    # ---------- the contractor's own certificate: an independent reading of the same work ----
    checked = agreed = 0
    for d in corpus.ids("company_completion_certificate"):
        cc = P.parse_company_certificate(corpus, d)
        w = works.get(cc["pkg"])
        if not w:
            bad(f"company certificate {d}: no matching work (ref {cc['cert_ref']})")
            continue
        if cc["value"] is not None:
            checked += 1
            if cc["value"] == w["value"]:
                agreed += 1
            else:
                bad(f"pkg {cc['pkg']}: value {w['value']} (client cert) vs {cc['value']} "
                    f"(company cert)")
        if cc["manager"] and P.norm_person(cc["manager"]) != w["manager_key"]:
            bad(f"pkg {cc['pkg']}: manager {w['manager']!r} vs {cc['manager']!r} (company cert)")
    store["value_crosscheck"] = (agreed, checked)

    # ---------- reference letters: presence is itself an answer ----------
    for d in corpus.ids("reference_letter"):
        r = P.parse_reference_letter(corpus, d)
        if r["pkg"] is None:
            bad(f"reference letter {d}: no work resolved")
            continue
        w = works.get(r["pkg"])
        if not w:
            bad(f"reference letter {d}: pkg {r['pkg']} is not a completed work")
            continue
        w["has_reference_letter"] = True
        w["reference_docs"].append(d)
        if r["role"] and w["role"] and r["role"] != w["role"]:
            bad(f"pkg {r['pkg']}: role {w['role']!r} (portfolio) vs {r['role']!r} (letter)")

    # ---------- people ----------
    creds = []
    for d in corpus.ids("personnel_certificate"):
        c = P.parse_personnel_certificate(corpus, d)
        if not c["holder"]:
            bad(f"personnel certificate {d}: no holder parsed")
            continue
        c["holder_key"] = P.norm_person(c["holder"])
        creds.append(c)

    cvs = []
    for d in corpus.ids("cv"):
        c = P.parse_cv(corpus, d)
        if not c["name"]:
            bad(f"cv {d}: no name parsed")
            continue
        c["name_key"] = P.norm_person(c["name"])
        cvs.append(c)

    # ---------- money owed ----------
    invoices = P.parse_receivables(corpus)
    for iv in invoices:
        iv["client_key"] = P.norm_client(iv["client_name"])

    # ---------- bids and guarantees ----------
    bonds = [P.parse_performance_bond(corpus, d) for d in corpus.ids("performance_bond")]
    dossiers = [P.parse_tender_dossier(corpus, d) for d in corpus.ids("tender_dossier")]

    store.update(works=works, credentials=creds, cvs=cvs, invoices=invoices,
                 bonds=bonds, dossiers=dossiers, dropped_certs=dropped)
    return store


def report(store, log=print):
    """What was parsed, and whether the independent readings of it agree.

    None of this is a precondition for answering: it describes the estate we were handed, and an
    estate we have not seen will legitimately produce different numbers. It is here so that a
    parser that has silently stopped working is visible in the run log rather than only in the
    score.
    """
    w = store["works"]
    vals = [x["value"] for x in w.values()]
    agreed, checked = store["value_crosscheck"]
    log(f"[store] works                  {len(w)}")
    log(f"[store] total contract value   {sum(vals) if vals else 0}")
    log(f"[store] distinct clients       {len({x['client_key'] for x in w.values()})}")
    log(f"[store] distinct managers      {len({x['manager_key'] for x in w.values()})}")
    log(f"[store] categories             {len({x['category'] for x in w.values()})}")
    log(f"[store] with reference letter  {sum(x['has_reference_letter'] for x in w.values())}"
        f" of {len(w)}")
    log(f"[store] roles known            {sum(bool(x['role']) for x in w.values())} of {len(w)}")
    log(f"[store] value cross-check      {agreed}/{checked} company certificates agree with the "
        f"client's")
    log(f"[store] credentials {len(store['credentials'])}  cvs {len(store['cvs'])}  "
        f"invoices {len(store['invoices'])}  bonds {len(store['bonds'])}  "
        f"dossiers {len(store['dossiers'])}")
    if store["dropped_certs"]:
        log(f"[store] WARNING {store['dropped_certs']} completion certificates were unparseable "
            f"and their works are missing from the store")
    if checked and agreed < checked:
        log(f"[store] WARNING {checked - agreed} works where the two certificates disagree on "
            f"value; the client's certificate is used")
    missing_role = sum(1 for x in w.values() if not x["role"])
    if missing_role:
        log(f"[store] NOTE {missing_role} works have no portfolio entry, so their Prime/JV role "
            f"is unknown")
    if store["issues"]:
        log(f"[store] {len(store['issues'])} cross-validation issues; first few:")
        for m in store["issues"][:10]:
            log(f"[store]    {m}")


SCHEMA = """
  CREATE TABLE work(pkg INTEGER PRIMARY KEY, cert_doc TEXT, cert_ref TEXT, client_id INTEGER,
    client_name TEXT, client_key TEXT, state TEXT, work_name TEXT, category TEXT,
    value TEXT, completed TEXT, manager TEXT, manager_key TEXT, grading TEXT,
    role TEXT, family TEXT, has_reference_letter INTEGER, reference_docs TEXT);
  CREATE TABLE credential(doc_id TEXT, holder TEXT, holder_key TEXT, employee_id TEXT,
    credential TEXT, credential_id TEXT, issued TEXT);
  CREATE TABLE cv(doc_id TEXT, name TEXT, name_key TEXT, employee_id TEXT, designation TEXT,
    business_unit TEXT, experience TEXT, joined TEXT);
  CREATE TABLE invoice(invoice TEXT, client_name TEXT, client_key TEXT, date TEXT,
    invoiced TEXT, received TEXT, outstanding TEXT, status TEXT);
  CREATE TABLE bond(doc_id TEXT, bond_no TEXT, issued TEXT, tender_ref TEXT, amount TEXT,
    work_desc TEXT, bank TEXT);
  CREATE TABLE dossier(doc_id TEXT, tender_ref TEXT, bid_value TEXT, submitted TEXT, client TEXT);
  CREATE INDEX ix_work_client ON work(client_key);
  CREATE INDEX ix_work_mgr ON work(manager_key);
  CREATE INDEX ix_inv_client ON invoice(client_key);
"""


def persist(store, db_path):
    db_path.unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO work VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   [(w["pkg"], w["cert_doc"], w["cert_ref"], w["client_id"], w["client_name"],
                     w["client_key"], w["state"], w["work_name"], w["category"], str(w["value"]),
                     str(w["completed"]), w["manager"], w["manager_key"], w["grading"], w["role"],
                     w["family"], int(w["has_reference_letter"]), ",".join(w["reference_docs"]))
                    for w in store["works"].values()])
    db.executemany("INSERT INTO credential VALUES (?,?,?,?,?,?,?)",
                   [(c["doc_id"], c["holder"], c["holder_key"], c["employee_id"], c["credential"],
                     c["credential_id"], str(c["issued"])) for c in store["credentials"]])
    db.executemany("INSERT INTO cv VALUES (?,?,?,?,?,?,?,?)",
                   [(c["doc_id"], c["name"], c["name_key"], c["employee_id"], c["designation"],
                     c["business_unit"], c["experience"], str(c["joined"]))
                    for c in store["cvs"]])
    db.executemany("INSERT INTO invoice VALUES (?,?,?,?,?,?,?,?)",
                   [(str(i["invoice"]), i["client_name"], i["client_key"], str(i["date"]),
                     str(i["invoiced"]), str(i["received"]), str(i["outstanding"]),
                     str(i["status"])) for i in store["invoices"]])
    db.executemany("INSERT INTO bond VALUES (?,?,?,?,?,?,?)",
                   [(b["doc_id"], b["bond_no"], str(b["issued"]), b["tender_ref"],
                     str(b["amount"]), b["work_desc"], b["bank"]) for b in store["bonds"]])
    db.executemany("INSERT INTO dossier VALUES (?,?,?,?,?)",
                   [(d["doc_id"], d["tender_ref"], str(d["bid_value"]), str(d["submitted"]),
                     d["client"]) for d in store["dossiers"]])
    db.commit()
    db.close()


def build_and_persist(corpus, db_path, log=print):
    store = build(corpus, log=log)
    report(store, log=log)
    if not store["works"]:
        sys.exit("[store] no works parsed — the fact store would be empty, refusing to write")
    persist(store, db_path)
    log(f"[store] wrote {db_path}")
    return store
