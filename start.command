#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, pydantic, httpx, uvicorn; assert int(pydantic.__version__.split(".")[0]) == 2' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
echo 'IASC Cross-Rail PoC: buka http://127.0.0.1:8000 — Ctrl+C untuk berhenti.'
exec .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
