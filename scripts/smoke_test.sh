#!/usr/bin/env bash
set -euo pipefail

echo "[smoke] import core package"
PYTHONPATH="$(pwd)/src" python3 -c "import scraper; import scraper.cli; print('ok')"

echo "[smoke] show available sites"
PYTHONPATH="$(pwd)/src" python3 -c "from scraper.cli import SITES; print(sorted(SITES.keys()))"

echo "[smoke] done"

