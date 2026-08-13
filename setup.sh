#!/usr/bin/env bash
# Prepare the environment, before the timed run.
#
# There is deliberately nothing to download here. This pipeline ships no model weights: the only
# generative model it uses is the endpoint the organisers provide, and everything else — parsing,
# entity resolution, arithmetic — is code in this repository. That is the whole reason nothing can
# quietly fetch itself on first use inside run.sh.
#
# What this script does is fail loudly, now, if anything run.sh needs is missing.
set -euo pipefail

# A candidate has to actually run, not merely be on PATH: Windows ships a `python3` shim that
# exists, resolves, and then refuses to execute anything.
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  for c in python3 python python3.12 python3.11; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
      PY="$c"; break
    fi
  done
fi
if [[ -z "$PY" ]]; then echo "setup.sh: no python interpreter found" >&2; exit 127; fi

echo "setup.sh: $($PY --version 2>&1)"

"$PY" - <<'PYCODE'
import sys

if sys.version_info < (3, 9):
    sys.exit(f"setup.sh: python 3.9+ required, found {sys.version.split()[0]}")

missing = []
for mod, why in (("fitz", "PyMuPDF — the PDF text layer"),
                 ("openpyxl", "XLSX workbooks")):
    try:
        __import__(mod)
    except ImportError:
        missing.append(f"  {mod}: {why}")
if missing:
    sys.exit("setup.sh: missing dependencies (pip install -r requirements.txt):\n"
             + "\n".join(missing))

import fitz, openpyxl                                                   # noqa: E402
print(f"setup.sh: PyMuPDF {fitz.__doc__.strip().splitlines()[0] if fitz.__doc__ else ''} "
      f"openpyxl {openpyxl.__version__}")

# The plan vocabulary and the rule table are imported here so that a syntax error or a bad regex
# surfaces during setup rather than forty minutes into a graded run.
sys.path.insert(0, ".")
from pipeline import anchor, dsl, ingest, llm, parse, planner, rules, solve, store   # noqa: E402,F401
print(f"setup.sh: pipeline imports clean; {len(dsl.AGGS)} aggregates, "
      f"{len(rules.SHAPES)} question shapes")
PYCODE

echo "setup.sh: ready. run.sh needs no network except \$LLM_BASE_URL."
