#!/usr/bin/env bash
# Entry point for the systemd timer. Waits for network, then runs sync.sh.
# Uses the personal SSH key explicitly so it works without a logged-in ssh-agent.
# Shows a desktop notification on success (with new-commit counts) and on failure.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/github_personal -o IdentitiesOnly=yes -o BatchMode=yes"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"

# notify_fail "<reason>" ["<error output from the failed step>"]
notify_fail() {
  local reason="$1" detail="${2:-}"
  echo "[$(date -Is)] FAILED: $reason${detail:+ ($detail)}" >&2
  mkdir -p "$HERE/logs"
  echo "$(date '+%F %T,000') ERROR scheduled sync failed: $reason${detail:+ -- $detail}" >> "$HERE/logs/mirror.log" 2>/dev/null
  notify-send -u critical -a contribution-mirror -i dialog-error \
    "Contribution mirror sync failed" "$reason${detail:+
$detail}
Log: $HERE/logs/mirror.log" 2>/dev/null || true
}

# Wait up to ~5 min for GitHub to be reachable (laptop may have just booted / be offline).
online=0
for _ in $(seq 30); do
  if timeout 5 bash -c 'echo > /dev/tcp/github.com/22' 2>/dev/null; then online=1; break; fi
  sleep 10
done
[[ $online -eq 1 ]] || { notify_fail "GitHub not reachable after 5 minutes (will retry at next run)"; exit 1; }

echo "[$(date -Is)] scheduled sync starting"
out="$(mktemp)"; trap 'rm -f "$out"' EXIT
"$HERE/sync.sh" 2>&1 | tee "$out"
rc=${PIPESTATUS[0]}
if [[ $rc -ne 0 ]]; then
  # Last lines of the output hold the actual error (stderr is merged into it).
  detail="$(grep -v '^[[:space:]]*$' "$out" | tail -3 | sed ':a;N;$!ba;s/\n/ ; /g')"
  notify_fail "sync.sh exited with code $rc (commits may not be pushed)" "$detail"
  exit "$rc"
fi

# Success notification: total new commits, plus per-repo breakdown for repos that got any.
total="$(sed -n 's/^created \([0-9]*\) commits.*/\1/p' "$out" | tail -1)"
total="${total:-0}"
detail="$(sed -n 's#^repo \(.*/\)\{0,1\}\([^/]*\): .* new=\([0-9][0-9]*\)$#\2: \3#p' "$out" | grep -v ': 0$' || true)"
if [[ "$total" -gt 0 ]]; then
  msg="$total new commit(s) mirrored and pushed
$detail"
else
  msg="Up to date, no new commits."
fi
notify-send -u normal -a contribution-mirror -i emblem-ok-symbolic \
  "Contribution mirror sync OK" "$msg" 2>/dev/null || true
