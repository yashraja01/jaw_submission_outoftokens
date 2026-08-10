"""Client-resolution regression suite.

Every case here was a real defect or a near-miss found by hand-checking. The failure mode is
silent — a plausible client resolves, the arithmetic is correct, and the answer is wrong — so it
is pinned here rather than left to the audit to rediscover.

    python eval/test_resolution.py
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from qa.resolve import Facts, norm_q                                    # noqa: E402

if hasattr(sys.stdout, "reconfigure"):   # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8")

# qid -> the client the question is really about
CASES = {
    # question names the client; the work match is incidental
    "HV-IC-0018": "Irrigation & Waterways Dept, Govt of West Bengal",
    "HV-IC-0043": "Public Works Department, Govt of Gujarat",
    "HV-IC-0049": "Public Works Department, Govt of Maharashtra",
    "HV-IC-0127": "Irrigation & Waterways Dept, Govt of West Bengal",
    "HV-IC-0155": "Public Works Department, Govt of Gujarat",
    "HV-IC-0196": "Public Works Department, Govt of Gujarat",
    "HV-IC-0344": "Irrigation & Waterways Dept, Govt of West Bengal",
    "HV-IC-0041": "Irrigation & Waterways Dept, Govt of Uttar Pradesh",   # "the UP irrigation account"
    # work-name words must not select a client: "Steel Truss Bridge" != Mahanadi Steel Corporation,
    # "Highway Construction" != Lakshya ... & Construction, "Drainage Works" != Public Works Dept
    "HV-IC-0054": "Trishakti Power Generation Corporation",
    "HV-IC-0271": "Trishakti Power Generation Corporation",
    "HV-IC-0073": "Arunodaya Infrastructure",
    "HV-IC-0097": "Subarnarekha Valley Corporation",
    "HV-IC-0072": "Suvarna Projects Limited",
    "HV-IC-0193": "Lakshya Engineering & Construction",
    "HV-IC-0194": "Mahanadi Steel Corporation",
    # gold-confirmed from the README's format example
    "HV-IC-0001": "National Expressway Development Authority",
    "HV-IC-0002": "Irrigation & Waterways Dept, Govt of Rajasthan",
    "HV-IC-0003": "Public Works Department, Govt of West Bengal",
    # a state mentioned via a work must not beat a named client
    "HV-IC-0260": "Trishakti Power Generation Corporation",
    # bare distinctive token, legal name omitted
    "HV-IC-0386": "Trishakti Power Generation Corporation",
    "HV-IC-0220": "Peninsular Petroleum Corporation",
}


def main():
    f = Facts()
    qs = {q["qid"]: q["question"]
          for q in json.load(open(ROOT / "dataset" / "questions.json", encoding="utf-8"))["questions"]}
    bad = []
    for qid, want in CASES.items():
        if qid not in qs:
            continue                                   # withdrawn from the current release
        t = qs[qid]
        w = f.resolve_work(t)
        explicit = re.search(r"\b(?:pkg|package)\s*\d+", norm_q(t)) is not None
        got = f.resolve_client(t, mask_work=w if explicit else None) or f.client_of_work(w)
        if not got or got["name"] != want:
            bad.append((qid, got["name"] if got else None, want))
    n = sum(1 for q in CASES if q in qs)
    for qid, got, want in bad:
        print(f"  FAIL {qid}: got {got!r}, want {want!r}")
    print(f"client resolution: {n - len(bad)}/{n} cases pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
