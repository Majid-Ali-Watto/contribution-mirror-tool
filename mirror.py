#!/usr/bin/env python3
"""Mirror commit TIMESTAMPS (only) from local repos into an empty-commit repo.

Reads from source repos: author date of commits matching --work-email. Nothing else
(no hashes, messages, paths, repo names) is read into the output or written anywhere.
Writes: empty commits with a generic message into --mirror. Never pushes.
Dry run by default; pass --apply to create commits.

Each run also appends one SUMMARY line to a LOCAL log file (logs/mirror.log): repos scanned and
new commits per repo (plus any warnings/errors). Repo paths appear only in that log, never in the mirror.
"""
import argparse, json, logging, logging.handlers, os, subprocess, sys
from collections import Counter

MSG = "mirror"
# Parent of the folder this script lives in -> its children are the "sibling folders".
SIBLINGS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repos.json")
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_MAX_BYTES = 5 * 1024 * 1024   # rotate to a new file at 5 MB
LOG_BACKUPS = 10                  # keep mirror.log + 10 older files (~55 MB worst case)
log = logging.getLogger("mirror")

def setup_log(log_dir):
    os.makedirs(log_dir, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(os.path.join(log_dir, "mirror.log"),
                                             maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                                             encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False

def say(msg):
    """Screen only. The log file gets one SUMMARY line per run (plus warnings/errors)."""
    print(msg)

def git(repo, *args, env=None, check=True):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          env=env, check=check).stdout

def source_dates(repo, emails):
    seen, dates = set(), []
    # %H used only in-memory to dedupe commits reachable from several branches.
    out = git(repo, "log", "--all", "--no-merges", "--format=%H %ae %aI", check=False)
    for line in out.splitlines():
        h, email, date = line.split(" ", 2)
        if h in seen:
            continue
        seen.add(h)
        if email.lower() in emails:
            dates.append(date)
    return dates

def load_repos_file(path):
    """Exact repo paths from a JSON file: {"repos": [...]}. No directory walking."""
    with open(path) as f:
        listed = json.load(f)["repos"]
    repos = []
    for r in listed:
        r = os.path.abspath(os.path.expanduser(r))
        if not os.path.exists(os.path.join(r, ".git")):
            print(f"warning: skipping '{r}' (not a git repo)", file=sys.stderr)
            log.warning("skipped %s (not a git repo)", r)
        elif r not in repos:
            repos.append(r)
    return repos

def find_repos(roots):
    for root in roots:
        if os.path.isdir(os.path.join(root, ".git")):
            yield root
            continue
        for dp, dn, _ in os.walk(root):
            if ".git" in dn:
                yield dp
                dn[:] = []  # don't descend into repos
            dn[:] = [d for d in dn if d not in ("node_modules", ".git")]

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--src", nargs="+",
                   help="repo(s) or folders containing repos (searched recursively). "
                        "Default: the exact repos listed in --repos-file, or if that file is missing, "
                        "all sibling folders of this script's folder")
    p.add_argument("--repos-file", default=REPOS_FILE,
                   help="JSON file {\"repos\": [paths]} of repos to scan, without searching deeper (default: repos.json next to this script)")
    p.add_argument("--work-email", nargs="+", required=True, help="your author email(s) in work repos")
    p.add_argument("--mirror", required=True, help="path of the personal mirror repo (created if missing)")
    p.add_argument("--name", required=True, help="author name for mirror commits")
    p.add_argument("--email", required=True, help="VERIFIED email on your personal GitHub account")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--log-dir", default=LOG_DIR,
                   help="folder for mirror.log (rotates at 5 MB, keeps 10 old files; default: logs/ next to this script)")
    a = p.parse_args()
    setup_log(a.log_dir)

    mirror = os.path.abspath(a.mirror)
    emails = {e.lower() for e in a.work_email}
    if a.src:
        repos = [os.path.abspath(r) for r in find_repos(a.src)]
    elif os.path.exists(a.repos_file):
        repos = load_repos_file(a.repos_file)
    else:
        repos = [os.path.abspath(r) for r in find_repos([SIBLINGS])]
    if any(mirror == r or mirror.startswith(r + os.sep) for r in repos):
        log.error("mirror must not be inside a source repo")
        sys.exit("mirror must not be inside a source repo")
    repos = [r for r in repos if r != mirror]  # never scan the mirror itself

    wanted, per_repo = Counter(), []
    for r in repos:
        c = Counter(source_dates(r, emails))
        per_repo.append((r, c))
        wanted.update(c)

    existing = Counter()
    if os.path.isdir(os.path.join(mirror, ".git")):
        for d in git(mirror, "log", "--format=%aI", check=False).split():
            existing[d] += 1
    todo = wanted - existing

    # Attribute new commits to repos: each repo first claims timestamps already in the mirror,
    # the rest are new. Totals equal `todo` exactly.
    left, by_repo = existing.copy(), []
    for r, c in per_repo:
        covered = c & left
        left -= covered
        by_repo.append(f"{os.path.basename(r)}={sum((c - covered).values())}")
        say(f"repo {r}: matching={sum(c.values())} already_mirrored={sum(covered.values())} "
            f"new={sum((c - covered).values())}")

    say(f"source repos scanned : {len(repos)}")
    say(f"matching commits     : {sum(wanted.values())}")
    say(f"already mirrored     : {sum((wanted & existing).values())}")
    say(f"to create            : {sum(todo.values())}")

    def summary(verb):
        line = (f"SUMMARY mode={'apply' if a.apply else 'dry-run'} repos={len(repos)} "
            f"matching={sum(wanted.values())} already_mirrored={sum((wanted & existing).values())} "
            f"{verb}={sum(todo.values())} | new per repo: {' '.join(by_repo) or '-'}")
        print(line)
        log.info(line)

    if not a.apply:
        summary("would_create")
        say("dry run - nothing written (use --apply)")
        return

    if not os.path.isdir(os.path.join(mirror, ".git")):
        os.makedirs(mirror, exist_ok=True)
        git(mirror, "init", "-b", "main")
    for date in sorted(todo.elements()):
        env = {**os.environ,
               "GIT_AUTHOR_NAME": a.name, "GIT_AUTHOR_EMAIL": a.email, "GIT_AUTHOR_DATE": date,
               "GIT_COMMITTER_NAME": a.name, "GIT_COMMITTER_EMAIL": a.email, "GIT_COMMITTER_DATE": date}
        git(mirror, "-c", "commit.gpgsign=false", "commit", "--allow-empty",
            "--no-verify", "-m", MSG, env=env)
    say(f"created {sum(todo.values())} commits in {mirror}")
    summary("created")
    print("Review with `git log`, then add YOUR PERSONAL remote and push yourself.")

if __name__ == "__main__":
    main()
