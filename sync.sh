#!/usr/bin/env bash
# Mirror new work-commit timestamps into the personal work-contributions repo and push.
# Usage: ./sync.sh                     (repos listed in repos.json: mirror + push)
#        ./sync.sh quiz-battle docs    (only these folders, searched recursively; sibling names or paths)
#        ./sync.sh --dry-run [folders] (preview only, no commits, no push)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
MIRROR="$HOME/work-contributions"
WORK_EMAIL="you@company.com"
NAME="Majid Ali"
PERSONAL_EMAIL="majidaliqau@gmail.com"
WORK_GH_USER="your-work-account"   # gh CLI account of the work login (for PR/review timestamps)
EXPECTED_REMOTE="git@github.com:Majid-Ali-Watto/work-contributions.git"

DRY=0
SRC=()
for a in "$@"; do
  if [[ "$a" == "--dry-run" ]]; then DRY=1
  elif [[ -d "$ROOT/$a" ]]; then SRC+=("$ROOT/$a")
  elif [[ -d "$a" ]]; then SRC+=("$(cd "$a" && pwd)")
  else echo "folder not found: $a" >&2; exit 1
  fi
done

args=(--work-email "$WORK_EMAIL" --mirror "$MIRROR"
      --name "$NAME" --email "$PERSONAL_EMAIL" --gh-user "$WORK_GH_USER")
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
