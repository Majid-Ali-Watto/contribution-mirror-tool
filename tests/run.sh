#!/usr/bin/env bash
# Run every test (offline: needs only python3 and git). On Windows: python -m unittest discover -s tests
set -e
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PY="${PYTHON:-$(command -v python3 || command -v python)}"
"$PY" -W error::ResourceWarning -m unittest discover -s tests 2>&1 | tail -4
