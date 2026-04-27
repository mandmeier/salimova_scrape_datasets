#!/usr/bin/env bash
set -euo pipefail

echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if command -v python3 >/dev/null 2>&1; then
  echo "python3: $(python3 -V 2>&1)"
else
  echo "python3: (not found)"
fi

if command -v pip >/dev/null 2>&1; then
  echo "pip: $(pip --version 2>&1)"
fi

if command -v playwright >/dev/null 2>&1; then
  echo "playwright_cli: $(playwright --version 2>&1 || true)"
fi

if command -v tesseract >/dev/null 2>&1; then
  echo "tesseract: $(tesseract --version 2>&1 | head -n 1)"
fi

