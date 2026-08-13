"""Score the content classifier against document_index.csv (visible corpus only, dev use)."""
import csv, collections, pathlib, sys, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipeline import ingest

ROOT = pathlib.Path(__file__).resolve().parent.parent
truth = {r["doc_id"]: r["doc_type"] for r in csv.DictReader(open(ROOT/"dataset/document_index.csv", encoding="utf-8"))}
rows = json.load(open(ROOT/"build/catalog.json", encoding="utf-8"))
conf = collections.Counter(); wrong = []
for r in rows:
    did = pathlib.Path(r["path"]).stem
    t = truth.get(did)
    if t is None: continue
    got = r["doc_type"]
    if t.endswith("workbook") or r["kind"] == "xlsx": continue
    conf[(t, got)] += 1
    if t != got: wrong.append((did, t, got, r.get("score"), r.get("runner_up")))
ok = sum(n for (a, b), n in conf.items() if a == b)
tot = sum(conf.values())
print(f"pdf classification: {ok}/{tot} = {ok/tot:.4f}")
for w in wrong[:40]: print("  MISS", w)
