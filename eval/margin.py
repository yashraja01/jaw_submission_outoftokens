"""Rank every question by how *close* its entity resolution was.

The leaderboard is an expensive oracle: one attempt buys roughly one linear equation.
Residual loss is now small enough that group-testing it would cost more attempts than we
have. This finds the same candidates offline.

For each question it reports:
  cmargin  top-1 minus top-2 client score (small = the client could plausibly be another)
  route    how the client was actually chosen (name match / via work / engineer's primary)
  mambig   the named engineer matched more than one manager record

A wrong client is the failure mode nothing else catches: correct arithmetic over the wrong
portfolio passes every range check and every distribution audit.

    python eval/margin.py            # ranked table, riskiest first
    python eval/margin.py --shape mean_minus_median
"""
import argparse
import json
import pathlib
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qa.resolve import Facts, norm_q                                  # noqa: E402
from qa import plan as planner                                        # noqa: E402


def client_scores(f, q, mask_work=None):
    """resolve_client's internal scoreboard, which the resolver itself discards."""
    nq = norm_q(q)
    if mask_work:
        for t in norm_q(mask_work["work_name"]).split():
            if t in planner.STOP if hasattr(planner, "STOP") else False:
                continue
            if t.isdigit() or t == "pkg":
                continue
            nq = re.sub(rf"\b{re.escape(t)}\b", " ", nq, count=1)
    out = {}
    for k, sig in f._client_sig.items():
        if not sig["core"]:
            continue
        hit = [t for t in sig["core"] if re.search(rf"\b{re.escape(t)}\b", nq)]
        if not hit:
            continue
        s = sum(sig["w"][t] for t in hit) / sum(sig["w"].values())
        if any(f._df.get(t, 1) == 1 and t not in f._workvocab for t in hit):
            s = max(s, 1.0)
        if sig["state"]:
            s += 0.3 if sig["state"] in nq else -0.5
        out[k] = s
    return sorted(out.items(), key=lambda kv: -kv[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shape")
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args()

    f = Facts()
    qs = json.load(open(ROOT / "dataset" / "questions.json", encoding="utf-8"))["questions"]
    der = {r["qid"]: r for r in json.load(open(ROOT / "build" / "derivations.json"),
                                          )}

    rows = []
    for q in qs:
        d = der.get(q["qid"], {})
        if a.shape and d.get("shape") != a.shape:
            continue
        text = q["question"]
        work = f.resolve_work(text)
        mgr = f.resolve_manager(text, hint_work=work)
        explicit = re.search(r"\b(?:pkg|package)\s*\d+", norm_q(text)) is not None
        sc = client_scores(f, text, mask_work=work if explicit else None)
        top1 = sc[0][1] if sc else 0.0
        top2 = sc[1][1] if len(sc) > 1 else 0.0
        named = f.resolve_client(text, mask_work=work if explicit else None)
        if named is not None:
            route = "name"
        elif work is not None:
            route = "via-work"
        elif mgr is not None:
            route = "primary-of-engineer"
        else:
            route = "none"
        margin = round(top1 - top2, 4) if named is not None else 0.0
        rows.append({
            "qid": q["qid"],
            "shape": d.get("shape", "?"),
            "route": route,
            "margin": margin,
            "top1": round(top1, 3),
            "cand1": sc[0][0] if sc else "-",
            "cand2": sc[1][0] if len(sc) > 1 else "-",
            "client": (d.get("client") or "-"),
            "answer": d.get("value"),
        })

    # riskiest first: anything not decided by a clear name match, then smallest margins
    order = {"none": 0, "primary-of-engineer": 1, "via-work": 2, "name": 3}
    rows.sort(key=lambda r: (order[r["route"]], r["margin"]))

    print(f"{'qid':12s} {'route':20s} {'marg':>6s} {'shape':20s} client -> runner-up")
    for r in rows[: a.top]:
        print(f"{r['qid']:12s} {r['route']:20s} {r['margin']:6.3f} {r['shape']:20s} "
              f"{r['client'][:38]:38s} | {r['cand2']}")

    out = ROOT / "build" / "margins.json"
    json.dump(rows, open(out, "w", encoding="utf-8"), indent=1, default=str)
    by = {}
    for r in rows:
        by[r["route"]] = by.get(r["route"], 0) + 1
    print(f"\n{len(rows)} questions   routes={by}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
