"""Command line: parse arguments and run the steps in order (scan -> PRs/reviews -> compare -> write)."""
import argparse
import json
import os
import signal
import sys
import threading
from collections import Counter

from . import providers, remotes as remote_urls
from .activity import fetch_activity
from .config import repos_path
from .credentials import Creds, explicit_choice
from .logsetup import close_log, redact, say, setup_log
from .mirror_repo import ensure_repo, existing_dates, write_commits
from .report import RunResult, attribute, print_totals, summary
from .repos import ReposFileError, group_by_scope, origin_urls, select_sources
from .runlock import RunLocked, run_lock
from .scan import clear_tmp, scan_all
from .settings import LOG_DIR, REPOS_FILE
from .state import known_hosts, load_state, preferred_accounts, save_state


def build_parser():
    p = argparse.ArgumentParser(prog="mirror.py", description="Mirror contribution timestamps into an empty-commit repo.")
    p.add_argument("--src", nargs="+",
                   help="repo(s) or folders containing repos (searched recursively). "
                        "Default: the exact projects listed in --repos-file")
    p.add_argument("--repos-file", default=None,
                   help="JSON file {\"repos\": [paths or URLs]} of projects to scan, without searching deeper "
                        f"(default: {REPOS_FILE})")
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
    p.add_argument("--dates-out", default="",
                   help="also write the dates per day (already mirrored, and new) to this JSON file, for a calendar view")
    p.add_argument("--clone-cache", default="",
                   help="keep the clones of URL entries in this folder and only fetch new commits on later runs "
                        "(default: temporary clones, deleted after each run)")
    p.add_argument("--log-dir", default=LOG_DIR,
                   help="folder for mirror.log (rotates at 5 MB, keeps 10 old files), api-state.json and the "
                        f"temporary clones (default: {LOG_DIR})")
    return p


def run(argv=None):
    """The core run: scan, compare, and (with --apply) write. Returns a RunResult."""
    a = build_parser().parse_args(argv)
    with run_lock(a.log_dir):
        setup_log(a.log_dir)
        try:
            return _run(a)
        finally:
            close_log()                  # Windows cannot delete a log file that is still open


def _run(a):
    mirror = os.path.abspath(a.mirror)
    emails = {e.lower() for e in a.work_email}
    providers.reset()                 # every run starts from the saved state, never from an earlier run in this process
    remote_urls.reset()

    # 1. what to scan, and who we are on GitHub
    st = load_state(a.log_dir)
    providers.remember(known_hosts(st))
    repos, remotes = select_sources(a.src, a.repos_file or repos_path(), mirror)
    repos_by_scope = group_by_scope(repos, remotes, origin_urls(repos))
    # the account chosen last time is reused if it still works, unless a full re-scan was asked for
    creds = Creds(repos_by_scope, a.gh_user, None if a.full_api else preferred_accounts(st))
    if a.no_fetch:
        remotes = []
    n_sources = len(repos) + len(remotes)

    # 2. commit dates from the work repos, the mirror's current content, then PRs and reviews
    tmp_root = os.path.join(a.log_dir, "tmp")
    clear_tmp(tmp_root)               # removes what a killed run left behind (safe: we hold the run lock)
    per_repo = scan_all(repos, remotes, emails, creds, a.no_fetch, a.clone_cache or None, tmp_root)
    existing = existing_dates(mirror)
    done, started = {}, None
    if not a.no_api:
        items, done, started = fetch_activity(creds, repos_by_scope, st, bool(existing), a.full_api,
                                              explicit_choice(a.gh_user))
        per_repo += items

    # 3. what is new
    wanted = Counter()
    for _, c in per_repo:
        wanted.update(c)
    todo = wanted - existing
    by_repo = attribute(per_repo, existing)
    print_totals(n_sources, wanted, existing, todo)
    if a.dates_out:
        write_days(a.dates_out, existing, todo)

    if not a.apply:
        summary(False, n_sources, wanted, existing, todo, by_repo, "would_create")
        say("dry run - nothing written (use --apply)")
        return RunResult(sum(todo.values()), by_repo, dry_run=True)

    # 4. write
    ensure_repo(mirror)
    if todo:
        write_commits(mirror, list(todo.elements()), a.name, a.email)
    if done or providers.probed():  # only after the commits are safely written
        save_state(a.log_dir, done, started, providers.probed())
    say(f"created {sum(todo.values())} commits in {mirror}")
    summary(True, n_sources, wanted, existing, todo, by_repo, "created")
    return RunResult(sum(todo.values()), by_repo, dry_run=False)


def write_days(path, existing, todo):
    """{"existing": {day: n}, "new": {day: n}}: the mirror's dates and the ones a run adds, per calendar day (the day
    in each timestamp's own time zone). Dates only, the same information the mirror itself holds."""
    def per_day(counter):
        days = Counter()
        for iso, n in counter.items():
            days[iso[:10]] += n
        return dict(sorted(days.items()))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"existing": per_day(existing), "new": per_day(todo)}, f)


def _console_safe():
    """A Windows console may not be UTF-8: never crash on a character, replace it instead."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def _exit_on_sigterm():
    """SIGTERM (the page's Cancel, a scheduler stopping the job) ends the process like Ctrl+C would: through every
    `finally`, so temporary clones are deleted and the log is closed. Python's default would just stop."""
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))


def main(argv=None):
    """Dispatch: `sync`, `scheduled`, `schedule`, `accounts`, `setup`, `ui`, or (no command) the core run."""
    argv = list(sys.argv[1:] if argv is None else argv)
    _console_safe()
    _exit_on_sigterm()
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
    if command == "accounts":
        from . import accounts
        return accounts.main(rest)
    if command == "setup":
        from .config import setup_main
        return setup_main(rest)
    if command == "ui":
        from . import ui
        return ui.main(rest)
    try:
        result = run(argv)
    except (ReposFileError, RunLocked, RuntimeError) as e:
        print(redact(e), file=sys.stderr)
        return 1
    if not result.dry_run:
        print("Review with `git log`, then push it yourself (or use `mirror.py sync`, which also pushes).")
    return 0
