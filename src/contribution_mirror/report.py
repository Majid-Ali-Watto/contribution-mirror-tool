"""What a run prints and logs: per-source attribution, totals, and the one-line SUMMARY; and reading them back (the
page parses a run's output and the log files with the patterns below, so the formats are defined in one place)."""
import os
import re
from dataclasses import dataclass, field

from .logsetup import log, redact, say

API_PREFIX = "api:"                 # labels of the PR / review sources: api:pull-requests, api:reviews (any host)
REMOTE_PREFIX = "remote:"           # labels of URL entries: remote:<repo name>
SUMMARY_RE = re.compile(r"^SUMMARY mode=(\S+) repos=(\d+) matching=(\d+) already_mirrored=(\d+) "
                        r"(?:would_create|created)=(\d+)")
SOURCE_RE = re.compile(r"^repo (.+): matching=(\d+) already_mirrored=(\d+) new=(\d+)$")
LOG_LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (\w+) (.*)$")
API_NAMES = {f"{API_PREFIX}pull-requests": "Pull requests", f"{API_PREFIX}reviews": "Reviews",
             f"{API_PREFIX}issues": "Issues opened"}


@dataclass
class RunResult:
    """What a run did, for the commands built on top of it (sync, scheduled)."""
    created: int = 0                                           # commits created (or that would be, in a dry run)
    by_source: list = field(default_factory=list)              # [(source name, new commits)]
    dry_run: bool = True


def attribute(per_repo, existing):
    """Each source first claims timestamps already in the mirror; the rest are new, so totals equal the number of
    commits to create exactly. Prints one line per source and returns [(name, new), ...]."""
    left, by_repo = existing.copy(), []
    for r, c in per_repo:
        covered = c & left
        left -= covered
        new = sum((c - covered).values())
        by_repo.append((short_name(r), new))
        say(f"repo {r}: matching={sum(c.values())} already_mirrored={sum(covered.values())} new={new}")
    return by_repo


def short_name(label):
    """A source label for the SUMMARY line: a repo folder's name, or the label without its api:/remote: prefix."""
    for prefix in (API_PREFIX, REMOTE_PREFIX):
        if label.startswith(prefix):
            return label[len(prefix):]
    return os.path.basename(label.rstrip("/\\")) or label


def print_totals(n_sources, wanted, existing, todo):
    say(f"source repos scanned : {n_sources}")
    say(f"matching contributions: {sum(wanted.values())}")
    say(f"already mirrored     : {sum((wanted & existing).values())}")
    say(f"to create            : {sum(todo.values())}")


def summary(apply, n_sources, wanted, existing, todo, by_repo, verb):
    """Print and log the SUMMARY line. `verb` is would_create (dry run) or created."""
    line = (f"SUMMARY mode={'apply' if apply else 'dry-run'} repos={n_sources} "
            f"matching={sum(wanted.values())} already_mirrored={sum((wanted & existing).values())} "
            f"{verb}={sum(todo.values())} | new per repo: {' '.join(f'{n}={c}' for n, c in by_repo) or '-'}")
    print(line)
    log.info(line)


def _summary(m):
    return {"mode": m.group(1), "repos": int(m.group(2)), "matching": int(m.group(3)), "mirrored": int(m.group(4)),
            "new": int(m.group(5))}


def parse_result(lines):
    """Totals and per-source counts from a run's output (the SUMMARY line and the `repo ...:` lines)."""
    out = {"sources": [], "summary": None, "pushed": None}
    for line in lines:
        m = SOURCE_RE.match(line)
        if m:
            name = m.group(1)
            name = API_NAMES.get(name, name) if name.startswith(API_PREFIX) else \
                name if name.startswith(REMOTE_PREFIX) else (os.path.basename(name.rstrip("/\\")) or name)
            out["sources"].append({"name": name, "matching": int(m.group(2)), "mirrored": int(m.group(3)),
                                   "new": int(m.group(4))})
        m = SUMMARY_RE.match(line)
        if m:
            out["summary"] = _summary(m)
        if line in ("pushed", "nothing to push"):
            out["pushed"] = line
    return out


def read_logs(log_dir, limit=400, max_runs=60):
    """The newest log lines (mirror.log and its rotated copies), newest first, and the SUMMARY of each run."""
    entries, runs = [], []
    files = [os.path.join(log_dir, "mirror.log")] + [os.path.join(log_dir, f"mirror.log.{i}") for i in range(1, 4)]
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            m = LOG_LINE_RE.match(line)
            if not m:
                continue
            when, level, message = m.group(1), m.group(2), redact(m.group(3))
            s = SUMMARY_RE.match(message)
            if s and len(runs) < max_runs:
                runs.append({"time": when, **_summary(s)})
            if len(entries) < limit:
                entries.append({"time": when, "level": level, "message": message})
        if len(entries) >= limit and len(runs) >= max_runs:
            break
    return {"dir": log_dir, "entries": entries, "runs": list(reversed(runs))}
