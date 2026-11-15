#!/usr/bin/env sh
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
python -m pip install -r requirements.txt
if [ ! -f data/emberlens.sqlite ]; then python scripts/seed_demo.py; fi
printf '\nOpen http://127.0.0.1:8000\n'
python -m uvicorn backend.main:app --reload
