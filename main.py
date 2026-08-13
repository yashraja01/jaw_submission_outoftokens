"""documents + questions -> submission.csv, end to end.

    python main.py --docs DIR --questions FILE --out submission.csv

Stages, in order, each printing progress as it goes:

    ingest   every PDF and XLSX under --docs, classified by content
    store    typed records joined into a fact store
    plan     each question read into an executable plan, by the language model and by rules
    solve    plans executed in exact arithmetic, the agreed value written out

The run is idempotent: re-running against the same documents rebuilds the same store and writes the
same CSV. `--reuse-build` skips ingestion when the cache is already there, which is for development
only — a graded run always starts from the documents.
"""
import argparse
import csv
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pipeline import dsl, solve, store                                        # noqa: E402
from pipeline.facts import Facts                                              # noqa: E402
from pipeline.ingest import ingest                                            # noqa: E402
from pipeline.parse import Corpus                                             # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent


def load_questions(path):
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    qs = data["questions"] if isinstance(data, dict) else data
    out = []
    for q in qs:
        qid = q.get("qid") or q.get("question_id") or q.get("id")
        if not qid:
            continue
        out.append({"qid": str(qid), "question": q.get("question") or q.get("text") or "",
                    "answer_type": (q.get("answer_type") or "money").strip().lower(),
                    "tier": q.get("tier")})
    return out


def write_csv(rows, path):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["question_id", "answer"])
        for qid, value in rows:
            w.writerow([qid, value])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs", required=True, help="root of the document estate (walked recursively)")
    ap.add_argument("--questions", required=True, help="questions JSON")
    ap.add_argument("--out", default="submission.csv", help="submission CSV to write")
    ap.add_argument("--build", default=str(ROOT / "build"), help="working directory for caches")
    ap.add_argument("--workers", type=int, default=0, help="ingestion processes (0 = auto)")
    ap.add_argument("--llm-samples", type=int, default=3,
                    help="plans to sample per question from the model (0 disables the model)")
    ap.add_argument("--llm-concurrency", type=int, default=8,
                    help="in-flight requests to the shared endpoint")
    ap.add_argument("--trust", choices=["consensus", "rules", "model"], default="consensus",
                    help="who wins when the planners disagree (default: the model must have at "
                         "least two agreeing plans to override the rules)")
    ap.add_argument("--llm-budget", type=float, default=2400,
                    help="seconds of wall clock the planning stage may spend on the endpoint")
    ap.add_argument("--reuse-build", action="store_true",
                    help="development only: reuse an existing extraction cache")
    ap.add_argument("--limit", type=int, default=0, help="development only: first N questions")
    a = ap.parse_args(argv)

    t0 = time.time()
    build = pathlib.Path(a.build)
    build.mkdir(parents=True, exist_ok=True)
    db_path = build / "facts.db"

    questions = load_questions(a.questions)
    if a.limit:
        questions = questions[:a.limit]
    tiers = {}
    for q in questions:
        tiers[q.get("tier") or "untiered"] = tiers.get(q.get("tier") or "untiered", 0) + 1
    print(f"[run] {len(questions)} questions ({', '.join(f'{k}={v}' for k, v in tiers.items())})")

    if a.reuse_build and (build / "catalog.json").exists() and db_path.exists():
        print("[run] reusing the existing extraction cache (--reuse-build)")
    else:
        ingest(a.docs, build, workers=a.workers or None)
        corpus = Corpus(build)
        store.build_and_persist(corpus, db_path)
    print(f"[run] corpus ready in {time.time() - t0:.1f}s")

    facts = Facts(db_path)
    print(f"[run] fact store: {len(facts.works)} works, {len(facts.clients)} clients, "
          f"{len(facts.managers)} engineers, {len(facts.invoices)} invoices")

    llm_plans = {}
    if a.llm_samples > 0:
        from pipeline import planner
        llm_plans = planner.plan_all(questions, facts, samples=a.llm_samples,
                                     concurrency=a.llm_concurrency,
                                     budget_seconds=a.llm_budget)
    else:
        print("[plan] language model disabled (--llm-samples 0); rules only")

    fb = solve.fallbacks(facts)
    rows, log = [], []
    counts = {"agreed": 0, "rules": 0, "llm": 0, "fallback": 0}
    for i, q in enumerate(questions, 1):
        value, record = solve.answer_question(q, facts, llm_plans=llm_plans.get(q["qid"]),
                                              fb=fb, trust=a.trust)
        out = dsl.coerce(value, q["answer_type"])
        rows.append((q["qid"], "" if out is None else out))
        record["emitted"] = out
        log.append(record)
        if record["source"] == "fallback":
            counts["fallback"] += 1
        elif record["source"].startswith("llm"):
            counts["llm"] += 1
        else:
            counts["rules"] += 1
        if record.get("distinct_values") and len(record["distinct_values"]) == 1:
            counts["agreed"] += 1
        if i % 25 == 0 or i == len(questions):
            print(f"[solve] {i}/{len(questions)} answered")

    write_csv(rows, a.out)
    (build / "run_log.json").write_text(json.dumps(log, indent=1, default=str), encoding="utf-8")
    print(f"[run] wrote {a.out} ({len(rows)} rows) and {build / 'run_log.json'}")
    print(f"[run] answered from: model {counts['llm']}, rules {counts['rules']}, "
          f"fallback {counts['fallback']}; every planner agreed on {counts['agreed']}")
    print(f"[run] total {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
