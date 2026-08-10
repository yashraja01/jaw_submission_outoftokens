"""Resolve entities, assert integrity, persist to SQLite + an in-memory fact store."""
import collections
import json
import pathlib
import sqlite3
import sys
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from facts import parse as P  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "build" / "facts.db"


def build():
    store = {"issues": []}
    bad = store["issues"].append

    # ---------- works ----------
    certs = {}
    for d in P.doc_ids("DOC-CC"):
        c = P.parse_completion_certificate(d)
        if c["pkg"] is None or c["value"] is None or c["completed"] is None:
            bad(f"completion certificate {d}: incomplete parse {c['work_name']!r}")
            continue
        certs[c["pkg"]] = c
    ppp = P.parse_portfolio()

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
            "value": c["value"],                       # certificate is authoritative for money
            "completed": c["completed"],
            "manager": c["manager"], "manager_key": P.norm_person(c["manager"]),
            "grading": c["grading"], "family": c["family"],
            "role": p.get("role"), "has_reference_letter": False, "reference_docs": [],
        }
        if p and p.get("category") and p["category"] != works[pkg]["category"]:
            bad(f"pkg {pkg}: category {c['category']!r} (cert) vs {p['category']!r} (portfolio)")
        if pkg not in ppp:
            bad(f"pkg {pkg}: no portfolio entry — role unknown")

    # ---------- company certificates: independent cross-check ----------
    for d in P.doc_ids("DOC-CCC"):
        cc = P.parse_company_certificate(d)
        w = works.get(cc["pkg"])
        if not w:
            bad(f"company certificate {d}: no matching work (ref {cc['cert_ref']})")
            continue
        if cc["value"] is not None and cc["value"] != w["value"]:
            bad(f"pkg {cc['pkg']}: value {w['value']} (cert) vs {cc['value']} (company cert)")
        if cc["manager"] and P.norm_person(cc["manager"]) != w["manager_key"]:
            bad(f"pkg {cc['pkg']}: manager {w['manager']!r} vs {cc['manager']!r} (company cert)")

    # ---------- reference letters ----------
    for d in P.doc_ids("DOC-REF"):
        r = P.parse_reference_letter(d)
        if r["pkg"] is None:
            bad(f"reference letter {d}: no work resolved")
            continue
        w = works.get(r["pkg"])
        if not w:
            bad(f"reference letter {d}: pkg {r['pkg']} not a completed work")
            continue
        w["has_reference_letter"] = True
        w["reference_docs"].append(d)
        if r["role"] and w["role"] and r["role"] != w["role"]:
            bad(f"pkg {r['pkg']}: role {w['role']!r} (portfolio) vs {r['role']!r} (reference letter)")

    # ---------- credentials ----------
    creds = []
    for d in P.doc_ids("DOC-PCERT"):
        c = P.parse_personnel_certificate(d)
        if not c["holder"] or not c["issued"]:
            bad(f"personnel certificate {d}: incomplete parse")
            continue
        c["holder_key"] = P.norm_person(c["holder"])
        creds.append(c)

    # ---------- CVs ----------
    cvs = []
    for d in P.doc_ids("DOC-CV"):
        c = P.parse_cv(d)
        if not c["name"] or not c["business_unit"]:
            bad(f"cv {d}: incomplete parse")
            continue
        c["name_key"] = P.norm_person(c["name"])
        cvs.append(c)

    # ---------- receivables ----------
    invoices = P.parse_receivables()
    for iv in invoices:
        iv["client_key"] = P.norm_client(iv["client_name"])

    store.update(works=works, credentials=creds, cvs=cvs, invoices=invoices)
    return store


# --------------------------------------------------------------------------- assertions
def assertions(store):
    w = store["works"]
    checks = []
    add = lambda name, got, want, ok=None: checks.append(
        (name, got, want, (got == want) if ok is None else ok))

    add("works", len(w), 155)
    add("Σ contract value", sum(x["value"] for x in w.values()), Decimal("55303999999"))
    # 28, not 29: the Phase-0 recon counted a null client_name as a 29th distinct value.
    add("distinct client names", len({x["client_key"] for x in w.values()}), 28)
    add("distinct client record ids", len({x["client_id"] for x in w.values()}), 60)
    add("reference letters linked", sum(len(x["reference_docs"]) for x in w.values()), 132)
    add("works with a reference letter", sum(x["has_reference_letter"] for x in w.values()), 132)
    add("works without one", sum(not x["has_reference_letter"] for x in w.values()), 23)
    add("works with >1 letter", sum(len(x["reference_docs"]) > 1 for x in w.values()), 0)
    add("roles: Prime", sum(x["role"] == "Prime" for x in w.values()), 96)
    add("roles: JV Partner", sum(x["role"] == "JV Partner" for x in w.values()), 59)
    add("categories", len({x["category"] for x in w.values()}), 13)
    add("works with a manager", sum(bool(x["manager"]) for x in w.values()), 155)
    add("pkg == certificate doc number",
        sum(x["cert_doc"] == f"DOC-CC-{x['pkg']:03d}" for x in w.values()), 155)
    add("grading present (table family only)", sum(bool(x["grading"]) for x in w.values()), 84)
    add("credentials", len(store["credentials"]), 48)
    add("CVs with a business unit", len(store["cvs"]), 39)
    add("invoices", len(store["invoices"]), 518)
    return checks


def persist(store):
    DB.unlink(missing_ok=True)
    db = sqlite3.connect(DB)
    db.executescript("""
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
      CREATE INDEX ix_work_client ON work(client_key);
      CREATE INDEX ix_work_mgr ON work(manager_key);
      CREATE INDEX ix_inv_client ON invoice(client_key);
    """)
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
                     c["business_unit"], c["experience"], str(c["joined"])) for c in store["cvs"]])
    db.executemany("INSERT INTO invoice VALUES (?,?,?,?,?,?,?,?)",
                   [(i["invoice"], i["client_name"], i["client_key"], str(i["date"]),
                     str(i["invoiced"]), str(i["received"]), str(i["outstanding"]), i["status"])
                    for i in store["invoices"]])
    db.commit()
    db.close()


def main():
    store = build()
    checks = assertions(store)
    print(f"{'assertion':38s} {'got':>14s} {'want':>14s}")
    failed = 0
    for name, got, want, ok in checks:
        failed += not ok
        print(f"{'  OK  ' if ok else '  FAIL'} {name:32s} {str(got):>14s} {str(want):>14s}")
    print(f"\ngate: {len(checks)-failed}/{len(checks)} assertions passed")
    if store["issues"]:
        print(f"\ncross-validation issues ({len(store['issues'])}):")
        for m in store["issues"][:25]:
            print("   ", m)
    persist(store)
    print(f"\nwrote {DB}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
