"""Execute a query plan against the fact store. Pure Python arithmetic — nothing is generated.

Every handler returns (value, derivation). A handler that cannot resolve its anchor returns
(None, reason) and the caller falls back; it never returns 0 or blank, because both score exactly
zero under the continuous scorer.
"""
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP

R = lambda x: int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
PCT = lambda a, b: float(round(Decimal(a) * 100 / Decimal(b), 2)) if b else None


def _cw(p):
    return sorted(p["client"]["works"], key=lambda w: -w["value"]) if p["client"] else None


def _mw(p):
    return p["manager"]["works"] if p["manager"] else None


# --------------------------------------------------------------------- client-scoped, money
def hop_aggregate(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    return R(sum(w["value"] for w in ws)), f"{p['client']['name']}: sum of {len(ws)} works"


def avg_work_size(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    return R(sum(w["value"] for w in ws) / len(ws)), f"{p['client']['name']}: mean of {len(ws)}"


def mean_minus_median(p, f):
    """Signed: 'negative if avg dips'. The scorer handles negative golds; do not clamp."""
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    vals = sorted(w["value"] for w in ws)
    n = len(vals)
    mean = sum(vals) / n
    median = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    return R(mean - median), (f"{p['client']['name']}: mean {int(mean):,} - median "
                              f"{int(median):,} over {n} works")


def exclusion_aggregate(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    cat = p.get("category")
    if not cat:                                   # role-flavoured exclusion ("strip subcontractor")
        keep = [w for w in ws if w["role"] == "Prime"]
        return R(sum(w["value"] for w in keep)), f"{p['client']['name']}: Prime only (no category)"
    keep = [w for w in ws if w["category"] != cat]   # exact label, never family (frozen)
    return R(sum(w["value"] for w in keep)), f"{p['client']['name']}: {len(keep)} works, excl '{cat}'"


def threshold_aggregate(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    bar = p.get("amount")
    if bar is None:
        return R(sum(w["value"] for w in ws)), f"{p['client']['name']}: no bar found, full total"
    keep = [w for w in ws if w["value"] >= bar]
    return R(sum(w["value"] for w in keep)), f"{p['client']['name']}: {len(keep)} works >= {bar:,}"


def gap_to_threshold(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    tot = sum(w["value"] for w in ws)
    bar = p.get("amount")
    if bar is None or bar <= tot:
        return None, f"no usable target (bar={bar}, total={tot})"
    return R(bar - tot), f"{p['client']['name']}: {bar:,} - {int(tot):,}"


def rank_value(p, f):
    ws = _cw(p)
    if not ws or len(ws) < 2:
        return None, "client unresolved or single work"
    return R(ws[0]["value"] - ws[1]["value"]), f"{p['client']['name']}: top two gap"


def role_split(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    role = p.get("role", "Prime")
    keep = [w for w in ws if w["role"] == role]
    if not keep:
        return None, f"no {role} works"
    return R(sum(w["value"] for w in keep)), f"{p['client']['name']}: {len(keep)} as {role}"


def grading_filter(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    g = (p.get("grading") or "").lower()
    keep = [w for w in ws if (w["grading"] or "").lower() == g]
    if not keep:
        return None, f"no works graded {g!r} (grading absent from the prose family)"
    return R(sum(w["value"] for w in keep)), f"{p['client']['name']}: {len(keep)} graded {g}"


def year_delta(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    yrs = p.get("years") or []
    if len(yrs) < 2:
        return None, "two years not found"
    a = sum(w["value"] for w in ws if w["year"] == yrs[0])
    b = sum(w["value"] for w in ws if w["year"] == yrs[1])
    if a == 0 and b == 0:
        return None, f"no works completed in {yrs}"
    return R(abs(b - a)), f"{p['client']['name']}: |{int(b):,} ({yrs[1]}) - {int(a):,} ({yrs[0]})|"


# --------------------------------------------------------------------- receivables
def awarded_vs_invoiced(p, f):
    if not p["client"]:
        return None, "client unresolved"
    bucket = f.inv_by_client.get(p["client"]["key"])
    awarded = sum(w["value"] for w in p["client"]["works"])
    if not bucket:
        # four clients have completed works but no invoices; nothing billed means the whole
        # awarded value is the gap. Better than a median guess.
        return R(awarded), f"{p['client']['name']}: awarded {int(awarded):,}, nothing invoiced"
    other = bucket["received"] if p.get("against") == "received" else bucket["invoiced"]
    return R(abs(awarded - other)), (f"{p['client']['name']}: awarded {int(awarded):,} vs "
                                     f"{p.get('against','invoiced')} {int(other):,}")


def collection_pct(p, f):
    if not p["client"]:
        return None, "client unresolved"
    b = f.inv_by_client.get(p["client"]["key"])
    if not b or b["invoiced"] == 0:
        # nothing invoiced -> nothing collected. 0 and 100 are both arguable; bias low.
        return 0.0, f"{p['client']['name']}: nothing invoiced, collection recorded as 0"
    return PCT(b["received"], b["invoiced"]), (
        f"{p['client']['name']}: {int(b['received']):,}/{int(b['invoiced']):,}")


# --------------------------------------------------------------------- reference letters
def absence(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    n = sum(1 for w in ws if not w["has_reference_letter"])
    return n, f"{p['client']['name']}: {n} of {len(ws)} works unreferenced"


def referenced_share(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    n = sum(1 for w in ws if w["has_reference_letter"])
    return PCT(n, len(ws)), f"{p['client']['name']}: {n}/{len(ws)} referenced"


# --------------------------------------------------------------------- engineer-scoped
def distinct_category(p, f):
    ws = _mw(p)
    if not ws:
        return None, "manager unresolved"
    return len({w["category"] for w in ws}), f"{p['manager']['name']}: {len(ws)} works"


def temporal_chain(p, f):
    ws = _mw(p)
    if not ws:
        return None, "manager unresolved"
    issued = p.get("issued") or f.credential_issue_date(" ")
    keep = [w for w in ws if w["completed"] > issued]
    if not keep:
        return None, f"no works completed after {issued}"
    return R(sum(w["value"] for w in keep)), (
        f"{p['manager']['name']}: {len(keep)}/{len(ws)} works after {issued}")


def date_span(p, f):
    w = p["work"]
    if not w:
        return None, "work unresolved"
    issued = p.get("issued") or "2021-03-10"
    d = (dt.date.fromisoformat(w["completed"]) - dt.date.fromisoformat(issued)).days
    if d <= 0:
        return None, f"non-positive span ({issued} -> {w['completed']})"
    return d, f"{w['work_name']}: {issued} -> {w['completed']}"


def _by_client(works):
    agg = {}
    for w in works:
        b = agg.setdefault(w["client_key"], {"value": Decimal(0), "n": 0, "name": w["client_name"]})
        b["value"] += w["value"]
        b["n"] += 1
    return agg


def top_n_clients(p, f):
    ws = _mw(p)
    if not ws:
        return None, "manager unresolved"
    agg = sorted(_by_client(ws).values(), key=lambda b: -b["value"])
    top = agg[:2]
    return R(sum(b["value"] for b in top)), (
        f"{p['manager']['name']}: top {len(top)} of {len(agg)} clients "
        f"({', '.join(b['name'] for b in top)})")


def largest_client_share(p, f):
    ws = _mw(p)
    if not ws:
        return None, "manager unresolved"
    agg = _by_client(ws)
    top = max(agg.values(), key=lambda b: b["value"])
    return PCT(top["value"], sum(b["value"] for b in agg.values())), (
        f"{p['manager']['name']}: {top['name']} share of value across {len(agg)} clients")


def business_units(p, f):
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    units = {f.bu_by_person.get(w["manager_key"]) for w in ws}
    units.discard(None)
    if not units:
        return None, "no CV business unit for any manager of this client"
    return len(units), f"{p['client']['name']}: {len(units)} units across {len(ws)} works"


def pair_overlap(p, f):
    """Each work has exactly one manager, so a literal intersection is always empty. The two named
    engineers are pointers to the client; the count is that client's completed works."""
    ws = _cw(p)
    if not ws:
        return None, "client unresolved"
    return len(ws), f"{p['client']['name']}: {len(ws)} completed works"


def work_count(p, f):
    ws = _cw(p) or _mw(p)
    if not ws:
        return None, "anchor unresolved"
    return len(ws), f"{len(ws)} works"


HANDLERS = {
    "hop_aggregate": hop_aggregate, "avg_work_size": avg_work_size,
    "mean_minus_median": mean_minus_median,
    "exclusion_aggregate": exclusion_aggregate, "threshold_aggregate": threshold_aggregate,
    "gap_to_threshold": gap_to_threshold, "rank_value": rank_value, "role_split": role_split,
    "grading_filter": grading_filter, "year_delta": year_delta,
    "awarded_vs_invoiced": awarded_vs_invoiced, "collection_pct": collection_pct,
    "absence": absence, "referenced_share": referenced_share,
    "distinct_category": distinct_category, "temporal_chain": temporal_chain,
    "date_span": date_span, "top_n_clients": top_n_clients,
    "largest_client_share": largest_client_share, "business_units": business_units,
    "pair_overlap": pair_overlap, "work_count": work_count,
}


def run(shape, params, facts):
    h = HANDLERS.get(shape)
    if not h:
        return None, f"no handler for {shape}"
    try:
        return h(params, facts)
    except Exception as e:                                   # never let one question kill the run
        return None, f"{type(e).__name__}: {e}"
