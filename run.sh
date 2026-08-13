#!/usr/bin/env bash
# Documents in, submission.csv out. The whole pipeline, from a clean checkout.
#
#   ./run.sh --docs /path/to/documents --questions /path/to/questions.json --out submission.csv
#
# Ingestion, indexing, retrieval, reasoning and CSV output all happen inside this script. Nothing
# is downloaded here: the only network call is to the LLM endpoint the organisers provide, at
# $LLM_BASE_URL. If that endpoint cannot be reached the run still completes — the rule-based
# planner answers every question on its own — so a network problem costs accuracy, never the
# submission.
set -euo pipefail

DOCS=""
QUESTIONS=""
OUT="submission.csv"
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --docs)      DOCS="$2"; shift 2 ;;
    --questions) QUESTIONS="$2"; shift 2 ;;
    --out)       OUT="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
    *)           EXTRA+=("$1"); shift ;;      # passed through to main.py
  esac
done

if [[ -z "$DOCS" || -z "$QUESTIONS" ]]; then
  echo "usage: ./run.sh --docs DIR --questions FILE [--out submission.csv]" >&2
  exit 2
fi
if [[ ! -d "$DOCS" ]]; then
  echo "run.sh: --docs '$DOCS' is not a directory" >&2
  exit 2
fi
if [[ ! -f "$QUESTIONS" ]]; then
  echo "run.sh: --questions '$QUESTIONS' is not a file" >&2
  exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
if [[ -z "$PY" ]]; then echo "run.sh: no python interpreter found" >&2; exit 127; fi

export PYTHONUNBUFFERED=1          # progress must reach the log as it happens, not at the end
export PYTHONIOENCODING=utf-8      # the corpus is full of rupee signs and em dashes

echo "run.sh: $($PY --version 2>&1), docs=$DOCS, questions=$QUESTIONS, out=$OUT"
echo "run.sh: LLM_BASE_URL=${LLM_BASE_URL:-<unset — the run will use the rule-based planner only>}"

exec "$PY" "$HERE/main.py" \
  --docs "$DOCS" \
  --questions "$QUESTIONS" \
  --out "$OUT" \
  --build "$HERE/build" \
  ${EXTRA[@]+"${EXTRA[@]}"}
