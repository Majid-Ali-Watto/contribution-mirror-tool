"""The mirror repo: reading the timestamps already in it and writing new empty commits (one git fast-import)."""
import os
import subprocess
from collections import Counter
from datetime import datetime, timedelta

from .gitutil import canonical_date, git, git_lines
from .settings import MSG

def raw_date(iso):
    """ISO-8601 -> git's raw '<unix> <+hhmm>' form, keeping the original UTC offset."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    off = dt.utcoffset() or timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    return int(dt.timestamp()), f"{sign}{abs(mins) // 60:02d}{abs(mins) % 60:02d}"

def write_commits(mirror, dates, name, email):
    """Create one empty commit per date on `main` with a single `git fast-import` process."""
    has_main = subprocess.run(["git", "-C", mirror, "rev-parse", "--verify", "-q", "refs/heads/main^0"],
                              capture_output=True).returncode == 0
    ident = f"{name} <{email}>"
    msg = MSG.encode()
    first = has_main
    with subprocess.Popen(["git", "-C", mirror, "fast-import", "--quiet", "--date-format=raw"],
                          stdin=subprocess.PIPE, stderr=subprocess.PIPE) as p:   # closes the pipes on exit
        try:
            for ts, tz in sorted(raw_date(d) for d in dates):
                p.stdin.write(f"commit refs/heads/main\nauthor {ident} {ts} {tz}\ncommitter {ident} {ts} {tz}\n"
                              f"data {len(msg)}\n".encode() + msg + b"\n")
                if first:   # first commit continues the existing branch instead of starting a new root
                    p.stdin.write(b"from refs/heads/main^0\n")
                    first = False
                p.stdin.write(b"\n")
            p.stdin.close()
        except BrokenPipeError:
            pass
        err = p.stderr.read().decode()
        rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"git fast-import failed: {err.strip()}")


def _has_ref(mirror, ref):
    return subprocess.run(["git", "-C", mirror, "rev-parse", "--verify", "-q", ref + "^{commit}"],
                          capture_output=True).returncode == 0


def dates_in(mirror, rev):
    """Counter of the author dates in `rev` (e.g. main, origin/main..main), in the one spelling used everywhere."""
    existing = Counter()
    for d in git_lines(mirror, "log", "--format=%aI", rev):
        existing[canonical_date(d)] += 1
    return existing


def existing_dates(mirror):
    """Counter of the author dates already on the mirror's `main` (the branch that is written and pushed). A repo
    with no commits yet is simply empty; any other git failure raises (loudly), because treating it as empty would
    duplicate every commit."""
    if os.path.isdir(os.path.join(mirror, ".git")) and _has_ref(mirror, "refs/heads/main"):
        return dates_in(mirror, "refs/heads/main")
    return Counter()


def reconcile(mirror, name, email):
    """Make local `main` contain everything already on the remote (refs/remotes/origin/main, fetched just before),
    so that a second computer, or a new clone of the mirror, never re-creates what is already there:
      - nothing local yet, or local is behind: local main moves to the remote's;
      - local is ahead (an earlier push failed): nothing to do;
      - both have new commits: local main moves to the remote's, and only the dates the remote does not have yet
        are written again on top (mirror commits are just dates, so nothing is lost and nothing is doubled).
    Returns a short description of what was done, or None."""
    remote = "refs/remotes/origin/main"
    if not _has_ref(mirror, remote):
        return None
    if not _has_ref(mirror, "refs/heads/main"):
        git(mirror, "update-ref", "refs/heads/main", remote)
        return "started from the remote's history"
    ancestor = lambda a, b: subprocess.run(["git", "-C", mirror, "merge-base", "--is-ancestor", a, b],
                                           capture_output=True).returncode == 0
    if ancestor(remote, "refs/heads/main"):
        return None
    if ancestor("refs/heads/main", remote):
        git(mirror, "update-ref", "refs/heads/main", remote)
        return "caught up with the remote"
    extra = dates_in(mirror, f"{remote}..refs/heads/main") - dates_in(mirror, remote)
    git(mirror, "update-ref", "refs/heads/main", remote)
    if extra:
        write_commits(mirror, list(extra.elements()), name, email)
    return f"local and remote both had new commits: rebuilt on the remote, {sum(extra.values())} kept"


def ensure_repo(mirror):
    """Create the mirror repo (branch main) if it does not exist yet."""
    if not os.path.isdir(os.path.join(mirror, ".git")):
        os.makedirs(mirror, exist_ok=True)
        git(mirror, "init", "-b", "main")
