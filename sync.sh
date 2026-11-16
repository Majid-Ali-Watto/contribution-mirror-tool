#!/usr/bin/env bash
# Mirror new work-commit timestamps into the personal work-contributions repo and push.
# Usage: ./sync.sh                     (repos listed in repos.json: mirror + push)
#        ./sync.sh quiz-battle docs    (only these folders, searched recursively; sibling names or paths)
#        ./sync.sh --dry-run [folders] (preview only, no commits, no push)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

# Private settings live in .env (git-ignored); see .env.example.
source "$HERE/load-env.sh"
load_env "${MIRROR_ENV_FILE:-$HERE/.env}"
require_vars WORK_EMAIL PERSONAL_NAME PERSONAL_EMAIL EXPECTED_REMOTE
MIRROR="${MIRROR_DIR:-$HOME/work-contributions}"
NAME="$PERSONAL_NAME"
read -ra WORK_EMAILS <<< "$WORK_EMAIL"   # one or several, space-separated

DRY=0
SRC=()
for a in "$@"; do
  if [[ "$a" == "--dry-run" ]]; then DRY=1
  elif [[ -d "$ROOT/$a" ]]; then SRC+=("$ROOT/$a")
  elif [[ -d "$a" ]]; then SRC+=("$(cd "$a" && pwd)")
  else echo "folder not found: $a" >&2; exit 1
  fi
done

args=(--work-email "${WORK_EMAILS[@]}" --mirror "$MIRROR"
      --name "$NAME" --email "$PERSONAL_EMAIL")
[[ -n "${WORK_GH_USER:-}" ]] && args+=(--gh-user "$WORK_GH_USER")
# No folders given: mirror.py scans exactly the repos listed in repos.json (or, if that
# file is missing, every sibling folder of this one).
[[ ${#SRC[@]} -gt 0 ]] && args+=(--src "${SRC[@]}")

if [[ $DRY -eq 1 ]]; then
  python3 -I "$HERE/mirror.py" "${args[@]}"
  exit 0
fi

python3 -I "$HERE/mirror.py" "${args[@]}" --apply

# Safety: only ever push to the personal mirror remote.
remote="$(git -C "$MIRROR" remote get-url origin)"
if [[ "$remote" != "$EXPECTED_REMOTE" ]]; then
  echo "refusing to push: origin is '$remote', expected '$EXPECTED_REMOTE'" >&2
  exit 1
fi
# Skip the SSH round trip when nothing is unpushed (origin/main is the ref recorded by the last push).
if [[ "$(git -C "$MIRROR" rev-parse main)" == "$(git -C "$MIRROR" rev-parse -q --verify refs/remotes/origin/main || true)" ]]; then
  echo "nothing to push"
  exit 0
fi
git -C "$MIRROR" push origin main
