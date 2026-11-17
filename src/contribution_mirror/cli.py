"""Command line: parse arguments and run the steps in order (scan -> PRs/reviews -> compare -> write)."""
import argparse
import os
import sys
from collections import Counter

from .activity import fetch_activity
from .credentials import Creds, explicit_choice
from .logsetup import close_log, say, setup_log
from .mirror_repo import ensure_repo, existing_dates, write_commits
from .report import RunResult, attribute, print_totals, summary
from .repos import group_by_scope, select_sources
from .scan import scan_all
from .settings import LOG_DIR, REPOS_FILE
from .state import save_state


def build_parser():
    p = argparse.ArgumentParser(prog="mirror.py", description="Mirror contribution timestamps into an empty-commit repo.")
    p.add_argument("--src", nargs="+",
                   help="repo(s) or folders containing repos (searched recursively). "
                        "Default: the exact projects listed in --repos-file, or if that file is missing, "
                        "all sibling folders of the project folder")
    p.add_argument("--repos-file", default=REPOS_FILE,
                   help="JSON file {\"repos\": [paths or URLs]} of projects to scan, without searching deeper "
                        "(default: config/repos.json)")
    p.add_argument("--work-email", nargs="+", required=True, help="your author email(s) in work repos")
    p.add_argument("--mirror", required=True, help="path of the personal mirror repo (created if missing)")
    p.add_argument("--name", required=True, help="author name for mirror commits")
    p.add_argument("--email", required=True, help="VERIFIED email on your personal GitHub account")
    p.add_argument("--gh-user", help="optional override: use this account of the gh CLI as your WORK account. "
                   "Normally not needed: the account that has your commits in the listed projects is found "
                   "automatically (WORK_GITHUB_TOKEN, GH_TOKEN, gh CLI, or your git credential helper)")
    p.add_argument("--no-api", action="store_true", help="do not mirror PRs and reviews (commits only)")
    p.add_argument("--full-api", action="store_true",
                   help="re-scan the whole 365-day window for PRs/reviews instead of only the recent days")
    p.add_argument("--no-fetch", action="store_true",
                   help="do not `git fetch` the work repos and do not scan URL entries (offline / fastest)")
    p.add_argument("--apply", action="store_true", help="actually create commits (default is a dry run)")
    p.add_argument("--log-dir", default=LOG_DIR,
                   help="folder for mirror.log (rotates at 5 MB, keeps 10 old files) and api-state.json; "
                        "default: logs/ in the project folder")
    return p


def run(argv=None):
    """The core run: scan, compare, and (with --apply) write. Returns a RunResult."""
    a = build_parser().parse_args(argv)
    setup_log(a.log_dir)
    try:
        return _run(a)
    finally:
        close_log()                      # Windows cannot delete a log file that is still open


def _run(a):
    mirror = os.path.abspath(a.mirror)
    emails = {e.lower() for e in a.work_email}

    # 1. what to scan, and who we are on GitHub
    repos, remotes = select_sources(a.src, a.repos_file, mirror)
    repos_by_scope = group_by_scope(repos, remotes)
    creds = Creds(repos_by_scope, a.gh_user)
    if a.no_fetch:
        remotes = []
    n_sources = len(repos) + len(remotes)

    # 2. commit dates from the work repos, the mirror's current content, then PRs and reviews
    per_repo = scan_all(repos, remotes, emails, creds, a.no_fetch)
    existing = existing_dates(mirror)
    who, started = None, None
    if not a.no_api:
        items, who, started = fetch_activity(creds, repos_by_scope, a.log_dir, bool(existing), a.full_api,
                                             explicit_choice(a.gh_user))
        per_repo += items

    # 3. what is new
    wanted = Counter()
    for _, c in per_repo:
        wanted.update(c)
    todo = wanted - existing
    by_repo = attribute(per_repo, existing)
    print_totals(n_sources, wanted, existing, todo)

    if not a.apply:
        summary(False, n_sources, wanted, existing, todo, by_repo, "would_create")
        say("dry run - nothing written (use --apply)")
        return RunResult(sum(todo.values()), by_repo, dry_run=True)

    # 4. write
    ensure_repo(mirror)
    if todo:
        write_commits(mirror, list(todo.elements()), a.name, a.email)
    if who:  # only after the commits are safely written
        save_state(a.log_dir, who, started)
    say(f"created {sum(todo.values())} commits in {mirror}")
    summary(True, n_sources, wanted, existing, todo, by_repo, "created")
    print("Review with `git log`, then add YOUR PERSONAL remote and push yourself.")
    return RunResult(sum(todo.values()), by_repo, dry_run=False)


def _console_safe():
    """A Windows console may not be UTF-8: never crash on a character, replace it instead."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv=None):
    """Dispatch: `sync`, `scheduled`, `schedule`, or (no command) the core run. Returns the exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    _console_safe()
    command, rest = (argv[0], argv[1:]) if argv else (None, [])
    if command == "sync":
        from . import sync
        return sync.main(rest)
    if command == "scheduled":
        from . import scheduled
        return scheduled.main(rest)
    if command == "schedule":
        from . import schedule
        return schedule.main(rest)
    run(argv)
    return 0
