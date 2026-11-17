#!/usr/bin/env bash
# What a scheduler runs: wait for the network, sync, notify. All the logic is in Python (scheduled.py).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-$(command -v python3 || command -v python)}"
exec "$PY" -I "$HERE/../src/mirror.py" scheduled "$@"
