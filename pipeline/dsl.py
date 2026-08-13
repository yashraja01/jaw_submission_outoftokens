"""The query language every answer is computed in, and its interpreter.

A plan is data, not code: an anchor (which client / engineer / work the question is about), one or
two measures over the fact store, and an operator that combines them. Both planners — the language
model and the rule-based backstop — emit this same structure, which is what makes them comparable:
two independent routes to the same executable object, and a disagreement between them is a signal
we can act on rather than two incomparable numbers.

Nothing in here generates anything. Every arithmetic step is exact `Decimal` over rows that were
parsed out of documents, because the tolerance we are scored against (0.5% on money, 0.05 on a
percentage, exact on a count) is far tighter than a language model's arithmetic.

    plan = {
      "anchor":  {"client": <mention|null>, "engineer": <mention|null>, "work": <mention|null>},
      "left":    <measure>,
      "right":   <measure|null>,
      "combine": "left" | "difference" | "abs_difference" | "percent" | "sum",
    }

    measure = {
      "source":  "works" | "invoices" | "constant",
      "scope":   "client" | "engineer" | "work" | "corpus",
      "filters": {...},
      "agg":     "sum_value" | "count" | ... ,
      "n":       <int>,        # rank / top-n aggregates
      "value":   <number>,     # source == "constant"
    }
"""
import datetime as dt
import re
from decimal import Decimal, ROUND_HALF_UP

# --------------------------------------------------------------------------- vocabulary
SOURCES = ("works", "invoices", "constant")
SCOPES = ("client", "engineer", "work", "corpus")
COMBINES = ("left", "difference", "abs_difference", "percent", "sum")

WORK_AGGS = (
    "sum_value", "mean_value", "median_value", "count", "max_value", "min_value",
    "nth_largest_value", "top_n_sum_by_client", "max_client_value", "distinct_categories",
    "distinct_clients", "distinct_managers", "distinct_business_units", "distinct_states",
    "days_since_anchor_date",
)
INVOICE_AGGS = ("sum_invoiced", "sum_received", "sum_outstanding", "count_invoices")
AGGS = WORK_AGGS + INVOICE_AGGS

FILTER_KEYS = (
    "categories_in", "categories_not_in", "role", "years_in", "completed_after",
    "completed_before", "min_value", "max_value", "has_reference_letter", "grading", "state",
    "status",
)

R = lambda x: int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

# A date filter that means "the date this question anchors on" — the issue date of the certificate
# it names — rather than a literal calendar date.
ANCHOR_DATE = "ANCHOR_DATE"


class PlanError(ValueError):
    """A plan that cannot be executed as written. Never fatal — the caller falls back."""


# --------------------------------------------------------------------------- normalisation
def _as_list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def normalise(raw):
    """Coerce a loosely-shaped plan (an LLM's, say) into the exact structure, or raise.

    Structured decoding guarantees the JSON parses, not that it means anything: an unknown
    aggregate or an invoice aggregate over the works table is a plan we must reject rather than
    execute into a plausible-looking wrong number.
    """
    if not isinstance(raw, dict):
        raise PlanError(f"plan is {type(raw).__name__}, not an object")
    anchor = raw.get("anchor") or {}
    if not isinstance(anchor, dict):
        raise PlanError("anchor is not an object")
    plan = {
        "anchor": {k: (str(anchor[k]).strip() or None) if anchor.get(k) else None
                   for k in ("client", "engineer", "work")},
        "left": _measure(raw.get("left")),
        "right": _measure(raw.get("right")) if raw.get("right") else None,
        "combine": (raw.get("combine") or "left").strip(),
        "note": str(raw.get("note") or "")[:400],
    }
    if plan["combine"] not in COMBINES:
        raise PlanError(f"unknown combine {plan['combine']!r}")
    if plan["combine"] != "left" and plan["right"] is None:
        raise PlanError(f"combine {plan['combine']!r} needs a right-hand measure")
    return plan


def _measure(raw):
    if not isinstance(raw, dict):
        raise PlanError("measure is not an object")
    src = (raw.get("source") or "works").strip()
    if src not in SOURCES:
        raise PlanError(f"unknown source {src!r}")
    if src == "constant":
        v = raw.get("value")
        if v is None:
            raise PlanError("constant measure has no value")
        return {"source": "constant", "value": Decimal(str(v))}

    agg = (raw.get("agg") or "").strip()
    if agg not in AGGS:
        raise PlanError(f"unknown aggregate {agg!r}")
    if src == "works" and agg in INVOICE_AGGS:
        raise PlanError(f"aggregate {agg!r} is not available over works")
    if src == "invoices" and agg not in INVOICE_AGGS:
        raise PlanError(f"aggregate {agg!r} is not available over invoices")

    scope = (raw.get("scope") or "client").strip()
    if scope not in SCOPES:
        raise PlanError(f"unknown scope {scope!r}")

    filters = raw.get("filters") or {}
    if not isinstance(filters, dict):
        raise PlanError("filters is not an object")
    unknown = set(filters) - set(FILTER_KEYS)
    if unknown:
        raise PlanError(f"unknown filter(s) {sorted(unknown)}")

    f = {}
    for k in ("categories_in", "categories_not_in"):
        vals = [str(x).strip().lower() for x in _as_list(filters.get(k)) if str(x).strip()]
        if vals:
            f[k] = vals
    if filters.get("years_in"):
        f["years_in"] = [int(y) for y in _as_list(filters["years_in"])]
    for k in ("completed_after", "completed_before"):
        if filters.get(k):
            v = str(filters[k])[:10]
            # A date the planner could not state literally — "the credential issue date", "when the
            # certificate was issued" — becomes a reference to whatever this question anchors on,
            # resolved at execution time against the certificate the question actually names.
            f[k] = v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else ANCHOR_DATE
    for k in ("min_value", "max_value"):
        if filters.get(k) is not None:
            f[k] = Decimal(str(filters[k]))
    if filters.get("has_reference_letter") is not None:
        f["has_reference_letter"] = bool(filters["has_reference_letter"])
    for k in ("role", "grading", "state", "status"):
        if filters.get(k):
            f[k] = str(filters[k]).strip()

    return {"source": src, "scope": scope, "agg": agg, "filters": f,
            "n": int(raw.get("n") or 2)}


# --------------------------------------------------------------------------- row selection
def _work_rows(measure, ctx):
    scope = measure["scope"]
    if scope == "corpus":
        rows, label = list(ctx.facts.works), "the whole estate"
    elif scope == "engineer":
        if not ctx.manager:
            raise PlanError("engineer scope but no engineer resolved")
        rows, label = list(ctx.manager["works"]), ctx.manager["name"]
    elif scope == "work":
        if not ctx.work:
            raise PlanError("work scope but no work resolved")
        rows, label = [ctx.work], ctx.work["work_name"]
    else:
        if not ctx.client:
            raise PlanError("client scope but no client resolved")
        rows, label = list(ctx.client["works"]), ctx.client["name"]
    return rows, label


def _date_bound(value, ctx):
    if value != ANCHOR_DATE:
        return value
    if not ctx.anchor_date:
        raise PlanError("the plan filters on the anchor date but none was resolved")
    return str(ctx.anchor_date)


def _apply_work_filters(rows, f, ctx):
    out = rows
    if "categories_in" in f:
        out = [w for w in out if w["category"] in f["categories_in"]]
    if "categories_not_in" in f:
        out = [w for w in out if w["category"] not in f["categories_not_in"]]
    if "role" in f:
        want = f["role"].lower()
        out = [w for w in out if (w["role"] or "").lower() == want]
    if "years_in" in f:
        out = [w for w in out if w["year"] in f["years_in"]]
    if "completed_after" in f:
        bound = _date_bound(f["completed_after"], ctx)
        out = [w for w in out if str(w["completed"]) > bound]
    if "completed_before" in f:
        bound = _date_bound(f["completed_before"], ctx)
        out = [w for w in out if str(w["completed"]) < bound]
    if "min_value" in f:
        out = [w for w in out if w["value"] >= f["min_value"]]
    if "max_value" in f:
        out = [w for w in out if w["value"] <= f["max_value"]]
    if "has_reference_letter" in f:
        out = [w for w in out if bool(w["has_reference_letter"]) is f["has_reference_letter"]]
    if "grading" in f:
        want = f["grading"].lower()
        out = [w for w in out if (w["grading"] or "").lower() == want]
    if "state" in f:
        want = f["state"].lower()
        out = [w for w in out if (w["state"] or "").lower() == want]
    return out


def _invoice_rows(measure, ctx):
    f = measure["filters"]
    if measure["scope"] == "corpus":
        rows, label = list(ctx.facts.invoices), "the whole estate"
    else:
        if not ctx.client:
            raise PlanError("invoice measure but no client resolved")
        rows = [i for i in ctx.facts.invoices if i["client_key"] == ctx.client["key"]]
        label = ctx.client["name"]
    if "status" in f:
        want = f["status"].lower()
        rows = [i for i in rows if str(i["status"]).lower() == want]
    if "completed_after" in f:
        bound = _date_bound(f["completed_after"], ctx)
        rows = [i for i in rows if str(i["date"]) > bound]
    if "completed_before" in f:
        bound = _date_bound(f["completed_before"], ctx)
        rows = [i for i in rows if str(i["date"]) < bound]
    if "years_in" in f:
        rows = [i for i in rows if str(i["date"])[:4].isdigit()
                and int(str(i["date"])[:4]) in f["years_in"]]
    return rows, label


# --------------------------------------------------------------------------- aggregates
def _median(vals):
    v = sorted(vals)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _by_client(rows):
    agg = {}
    for w in rows:
        b = agg.setdefault(w["client_key"], {"value": Decimal(0), "n": 0, "name": w["client_name"]})
        b["value"] += w["value"]
        b["n"] += 1
    return agg


def _aggregate(measure, rows, ctx, label):
    agg, n = measure["agg"], max(1, measure["n"])
    vals = [w["value"] for w in rows]

    if agg == "count":
        return len(rows), f"{len(rows)} works ({label})"
    if agg == "distinct_categories":
        s = {w["category"] for w in rows if w["category"]}
        return len(s), f"{len(s)} distinct categories ({label})"
    if agg == "distinct_clients":
        s = {w["client_key"] for w in rows}
        return len(s), f"{len(s)} distinct clients ({label})"
    if agg == "distinct_managers":
        s = {w["manager_key"] for w in rows}
        return len(s), f"{len(s)} distinct engineers ({label})"
    if agg == "distinct_states":
        s = {w["state"] for w in rows if w["state"]}
        return len(s), f"{len(s)} distinct states ({label})"
    if agg == "distinct_business_units":
        units = {ctx.facts.bu_by_person.get(w["manager_key"]) for w in rows}
        units.discard(None)
        if not units:
            raise PlanError("no CV business unit for any engineer on these works")
        return len(units), f"{len(units)} business units across {len(rows)} works ({label})"
    if agg == "days_since_anchor_date":
        if len(rows) != 1:
            raise PlanError(f"a day span needs exactly one work, got {len(rows)}")
        if not ctx.anchor_date:
            raise PlanError("no anchor date for the day span")
        days = (dt.date.fromisoformat(str(rows[0]["completed"]))
                - dt.date.fromisoformat(str(ctx.anchor_date))).days
        return days, (f"{ctx.anchor_date} -> {rows[0]['completed']} "
                      f"({rows[0]['work_name']}): {days} days")

    if not rows:
        raise PlanError(f"no rows to aggregate ({label})")

    if agg == "sum_value":
        return sum(vals), f"sum of {len(rows)} works ({label})"
    if agg == "mean_value":
        return sum(vals) / len(vals), f"mean of {len(rows)} works ({label})"
    if agg == "median_value":
        return _median(vals), f"median of {len(rows)} works ({label})"
    if agg == "max_value":
        return max(vals), f"largest of {len(rows)} works ({label})"
    if agg == "min_value":
        return min(vals), f"smallest of {len(rows)} works ({label})"
    if agg == "nth_largest_value":
        if len(vals) < n:
            raise PlanError(f"asked for #{n} of only {len(vals)} works")
        return sorted(vals, reverse=True)[n - 1], f"#{n} largest of {len(rows)} ({label})"
    if agg == "top_n_sum_by_client":
        buckets = sorted(_by_client(rows).values(), key=lambda b: -b["value"])[:n]
        return (sum(b["value"] for b in buckets),
                f"top {len(buckets)} clients by value ({', '.join(b['name'] for b in buckets)})")
    if agg == "max_client_value":
        buckets = _by_client(rows)
        top = max(buckets.values(), key=lambda b: b["value"])
        return top["value"], f"largest client {top['name']} of {len(buckets)} ({label})"
    raise PlanError(f"unimplemented aggregate {agg!r}")


def _invoice_aggregate(measure, rows, label):
    agg = measure["agg"]
    if agg == "count_invoices":
        return len(rows), f"{len(rows)} invoices ({label})"
    if not rows:
        raise PlanError(f"no invoices on file ({label})")
    field = {"sum_invoiced": "invoiced", "sum_received": "received",
             "sum_outstanding": "outstanding"}[agg]
    total = sum(Decimal(str(i[field])) for i in rows)
    return total, f"{field} over {len(rows)} invoices ({label})"


def measure_value(measure, ctx):
    if measure["source"] == "constant":
        return measure["value"], f"the stated figure {measure['value']}"
    if measure["source"] == "invoices":
        rows, label = _invoice_rows(measure, ctx)
        return _invoice_aggregate(measure, rows, label)
    rows, label = _work_rows(measure, ctx)
    rows = _apply_work_filters(rows, measure["filters"], ctx)
    return _aggregate(measure, rows, ctx, label)


# --------------------------------------------------------------------------- execution
def execute(plan, ctx):
    """(value, derivation). Raises PlanError when the plan cannot be run against this corpus."""
    left, why_l = measure_value(plan["left"], ctx)
    combine = plan["combine"]
    if combine == "left":
        return left, why_l
    right, why_r = measure_value(plan["right"], ctx)

    if combine == "difference":
        return _num(left) - _num(right), f"{why_l} minus {why_r}"
    if combine == "sum":
        return _num(left) + _num(right), f"{why_l} plus {why_r}"
    if combine == "abs_difference":
        return abs(_num(left) - _num(right)), f"|{why_l} minus {why_r}|"
    if combine == "percent":
        if not _num(right):
            raise PlanError(f"percentage with a zero denominator ({why_r})")
        return _num(left) * 100 / _num(right), f"{why_l} as a percentage of {why_r}"
    raise PlanError(f"unknown combine {combine!r}")


def _num(v):
    return v if isinstance(v, Decimal) else Decimal(str(v))


# --------------------------------------------------------------------------- presentation
def coerce(value, answer_type):
    """The submission wants a plain number, in the unit the question declared."""
    if value is None:
        return None
    if answer_type == "percent":
        return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if answer_type in ("count", "days"):
        return R(value)
    return R(value)                                  # money: whole rupees


def describe(plan):
    """One line of English for the log, so a wrong answer can be read back to its plan."""
    def m(x):
        if x is None:
            return "-"
        if x["source"] == "constant":
            return f"const {x['value']}"
        bits = [f"{x['agg']} over {x['source']}/{x['scope']}"]
        for k, v in sorted(x["filters"].items()):
            bits.append(f"{k}={v}")
        return " ".join(bits)
    a = plan["anchor"]
    who = ", ".join(f"{k}={v!r}" for k, v in a.items() if v) or "no anchor mention"
    return f"[{who}] {plan['combine']}({m(plan['left'])}; {m(plan.get('right'))})"
