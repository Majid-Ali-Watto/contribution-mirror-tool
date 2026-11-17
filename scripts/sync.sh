#!/usr/bin/env bash
# Mirror new activity and push. Usage: scripts/sync.sh [--dry-run] [folder ...]
# A thin wrapper: all the logic is in Python (src/contribution_mirror/sync.py), so it behaves the same everywhere.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-$(command -v python3 || command -v python)}"
exec "$PY" -I "$HERE/../src/mirror.py" sync "$@"
