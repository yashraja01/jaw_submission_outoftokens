"""A stand-in for the vLLM endpoint, so the model path can be exercised without one.

Development only. It speaks enough of the OpenAI chat-completions API to drive `pipeline.planner`
end to end, and it can be told to misbehave in the ways the real server is documented to
misbehave, so the client's handling of each is tested rather than assumed:

    --flaky N       fail N% of requests with a 503
    --truncate N    return finish_reason "length" with content null on N% of requests
    --no-schema     reject response_format with a 400, as a server without structured output would
    --garbage N     return prose instead of JSON on N% of requests
    --wrong N       return a subtly wrong plan on N% of requests (a mean where a sum was asked
                    for, the whole estate where one client was asked for), to measure what the
                    reconciliation policy actually protects against

The plans it returns come from the rule-based planner, so a run against this server should land on
the same answers as a rules-only run. That is the point: it tests the plumbing — schema, parsing,
snapping, reconciliation, degradation — not the model's judgement, which cannot be tested without
the real endpoint.

    python dev/mock_llm.py --port 8112 &
    LLM_BASE_URL=http://127.0.0.1:8112/v1 python main.py --docs ... --questions ...
"""
import argparse
import json
import pathlib
import random
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pipeline import rules                                                    # noqa: E402
from pipeline.facts import Facts                                              # noqa: E402

OPTS = {}
FACTS = None


def wire_from_plan(plan):
    """Flatten an executable plan back into the wire shape the planner expects to receive."""
    def m(measure):
        if measure is None:
            return {"source": "none", "scope": "client", "agg": ""}
        if measure["source"] == "constant":
            return {"source": "constant", "scope": "client", "agg": "",
                    "value": float(measure["value"])}
        out = {"source": measure["source"], "scope": measure["scope"], "agg": measure["agg"],
               "n": measure.get("n", 2)}
        for k, v in measure["filters"].items():
            if k == "has_reference_letter":
                out[k] = "yes" if v else "no"
            elif k in ("min_value", "max_value"):
                out[k] = float(v)
            else:
                out[k] = v
        return out

    return {"reading": plan.get("note") or "rules-derived plan",
            "anchor_client": "", "anchor_engineer": "", "anchor_work": "",
            "left": m(plan["left"]), "right": m(plan.get("right")),
            "combine": plan["combine"]}


def perturb(plan, rng=random):
    """A plausible misreading, of the kind a model makes: the wrong aggregate or the wrong scope."""
    import copy
    p = copy.deepcopy(plan)
    left = p["left"]
    if left.get("source") == "works":
        if left.get("agg") == "sum_value":
            left["agg"] = rng.choice(["mean_value", "max_value", "median_value"])
        elif left.get("agg") == "count":
            left["agg"] = "distinct_categories"
        else:
            left["scope"] = "corpus"
    elif left.get("source") == "invoices":
        left["agg"] = "sum_outstanding" if left.get("agg") != "sum_outstanding" else "sum_invoiced"
    return p


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, body):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if OPTS["no_schema"] and "response_format" in body:
            return self._json(400, {"error": {"message": "response_format is not supported"}})
        if random.random() * 100 < OPTS["flaky"]:
            return self._json(503, {"error": {"message": "overloaded"}})

        messages = body.get("messages") or []
        user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        if random.random() * 100 < OPTS["truncate"]:
            return self._json(200, {"choices": [{"message": {"content": None, "reasoning": "..."},
                                                 "finish_reason": "length"}],
                                    "usage": {"total_tokens": 4096}})
        if random.random() * 100 < OPTS["garbage"]:
            return self._json(200, {"choices": [{"message": {"content": "I think the answer is 42."},
                                                 "finish_reason": "stop"}],
                                    "usage": {"total_tokens": 12}})

        qm = [l for l in user.splitlines() if l.startswith("question: ")]
        if not qm:                                   # the health probe
            return self._json(200, {"choices": [{"message": {"content": "ready"},
                                                 "finish_reason": "stop"}],
                                    "usage": {"total_tokens": 3}})
        question = qm[-1][len("question: "):]
        atype = next((l.split(": ", 1)[1] for l in user.splitlines()
                      if l.startswith("answer_type: ")), "money")
        plan, _shape, _matched = rules.plan(question, atype, FACTS)
        if OPTS["correlated"]:
            # A real model misreads the *same* question the same way every time it is asked, so
            # sampling it three times reproduces the misreading three times. Seeding on the
            # question makes the error deterministic and measures that honestly: it is the case
            # self-consistency cannot see.
            rng = random.Random(hash(question) & 0xffffffff)
            if rng.random() * 100 < OPTS["wrong"]:
                plan = perturb(plan, rng)
        elif random.random() * 100 < OPTS["wrong"]:
            plan = perturb(plan)
        return self._json(200, {"choices": [{"message": {"content": json.dumps(wire_from_plan(plan)),
                                                         "reasoning": "mock"},
                                             "finish_reason": "stop"}],
                                "usage": {"total_tokens": 250}})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8112)
    ap.add_argument("--db", default="build/facts.db")
    ap.add_argument("--flaky", type=float, default=0.0)
    ap.add_argument("--truncate", type=float, default=0.0)
    ap.add_argument("--garbage", type=float, default=0.0)
    ap.add_argument("--wrong", type=float, default=0.0)
    ap.add_argument("--correlated", action="store_true",
                    help="make the wrong plan deterministic per question")
    ap.add_argument("--no-schema", action="store_true")
    a = ap.parse_args()

    global FACTS
    FACTS = Facts(a.db)
    OPTS.update(flaky=a.flaky, truncate=a.truncate, garbage=a.garbage, wrong=a.wrong,
                correlated=a.correlated, no_schema=a.no_schema)
    print(f"mock endpoint on http://127.0.0.1:{a.port}/v1  "
          f"(flaky={a.flaky}% truncate={a.truncate}% garbage={a.garbage}% wrong={a.wrong}% "
          f"no_schema={a.no_schema})", flush=True)
    HTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
