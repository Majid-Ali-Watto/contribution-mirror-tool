"""The mirror repo: reading the timestamps already in it and writing new empty commits (one git fast-import)."""
import os
import subprocess
from collections import Counter
from datetime import datetime, timedelta

from .gitutil import git, git_lines
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


def existing_dates(mirror):
    """Counter of the author dates already in the mirror. A repo with no commits yet is simply empty; any other
    git failure raises (loudly), because treating it as empty would duplicate every commit."""
    existing = Counter()
    if os.path.isdir(os.path.join(mirror, ".git")) and subprocess.run(
            ["git", "-C", mirror, "rev-parse", "--verify", "-q", "HEAD"], capture_output=True).returncode == 0:
        for d in git_lines(mirror, "log", "--format=%aI"):
            existing[d] += 1
    return existing


def ensure_repo(mirror):
    """Create the mirror repo (branch main) if it does not exist yet."""
    if not os.path.isdir(os.path.join(mirror, ".git")):
        os.makedirs(mirror, exist_ok=True)
        git(mirror, "init", "-b", "main")
