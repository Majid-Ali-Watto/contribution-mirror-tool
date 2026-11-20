"""`doctor [--json]`: check the whole setup and say what is wrong and how to fix it.

Most "my graph is empty" reports are a setup problem the tool cannot see during a sync: an unverified personal email,
a mirror repo whose default branch is not `main`, a typo in the work email, a project that is not a repo, a
scheduled run that has been failing for weeks. Every check here only reads or asks: nothing is written, nothing is
fetched into a work repo, nothing is pushed. Exit code 1 if any check failed (warnings do not count)."""
import argparse
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta
from typing import NamedTuple

from . import accounts, envfile, mirror_repo, push
from .config import env_path, load_config, repos_path
from .gitutil import origin_url, source_commits
from .httpapi import Api, ApiError
from .logsetup import redact
from .remotes import is_url, repo_key, repo_path, url_host
from .report import read_logs
from .repos import ReposFileError, check_entry, read_entries
from .providers import provider_for
from .remotes import real_host
from .settings import LOG_DIR, command_name
from .state import state_path

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
STALE_DAYS = 3                  # a daily run that last succeeded longer ago than this is reported


class Check(NamedTuple):
    status: str                 # ok, warn, fail, skip (could not be checked)
    name: str
    detail: str
    fix: str = ""


def _private_problem(path):
    """A note if other users can read `path` (Linux/macOS), else None."""
    if os.name == "nt" or not os.path.exists(path):
        return None
    if stat.S_IMODE(os.stat(path).st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
        return f'chmod 600 "{path}"'
    return None


def check_settings(path):
    """(Check, Config or None): the settings file exists, has what is required, and is private."""
    try:
        cfg = load_config(path)
    except envfile.EnvFileError as e:
        fix = f"run `{command_name()} setup`, or fill it in on the page ({command_name()} ui)"
        return Check(FAIL, "Settings file", str(e), fix), None
    fix = _private_problem(path)
    if fix:
        return Check(WARN, "Settings file", f"{path} can be read by other users of this computer", fix), cfg
    return Check(OK, "Settings file", path), cfg


def check_git():
    try:
        out = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return Check(FAIL, "git", "git is not installed or cannot be started",
                     "install git and make sure it is on PATH")
    return Check(OK, "git", out or "installed")


def check_projects(path, mirror):
    """([Check], local repo paths, URLs) for the project list."""
    if not os.path.exists(path):
        return [Check(FAIL, "Project list", f"{path} does not exist",
                      f"run `{command_name()} setup`, or add projects on the page")], [], []
    try:
        entries, legacy = read_entries(path)
    except ReposFileError as e:
        return [Check(FAIL, "Project list", str(e), "fix the file, or save the list again on the page")], [], []
    if not entries:
        return [Check(FAIL, "Project list", f"{path} lists no projects",
                      "add your work repos (paths or git URLs)")], [], []
    checks, local, urls = [], [], []
    for entry in entries:
        st = check_entry(entry)
        if not st["ok"]:
            checks.append(Check(WARN, "Project", f"{entry}: {st['detail']}, skipped by every run",
                                "remove it from the list, or fix the path"))
        elif st["kind"] == "url":
            urls.append(entry.strip())
        else:
            path_ = os.path.abspath(os.path.expanduser(entry))
            if os.path.realpath(path_) == os.path.realpath(mirror):
                checks.append(Check(WARN, "Project", f"{entry} is the mirror itself, never scanned",
                                    "remove it from the list"))
            elif path_ not in local:
                local.append(path_)
    detail = f"{len(local)} local clone(s), {len(urls)} URL(s)"
    detail += " (uses the older remote_repos key)" if legacy else ""
    if not (local or urls):
        checks.insert(0, Check(FAIL, "Project list", f"{path}: none of the entries is a usable project of yours",
                               "add your work repos (paths or git URLs) and remove the examples"))
        return checks, local, urls
    fix = _private_problem(path)
    checks.insert(0, Check(WARN if fix else OK, "Project list", f"{path}: {detail}" +
                           (", readable by other users" if fix else ""), fix or ""))
    return checks, local, urls


def check_work_emails(emails, local, urls):
    """Your commits are found: per local clone, how many commits have one of `emails` (local data only, no fetch)."""
    if not local:
        return [Check(SKIP, "Work email", "no local clones to look in" + (
            "; URL entries are cloned and counted by the next sync" if urls else ""))]
    checks, total = [], 0
    who = " or ".join(sorted(emails))
    for repo in local:
        try:
            n = len(source_commits(repo, emails))
        except RuntimeError as e:
            checks.append(Check(WARN, "Work email", f"{repo}: could not read its commits: {e}"))
            continue
        total += n
        if n == 0:
            checks.append(Check(WARN, "Work email", f"{repo}: no commit by {who}",
                                f'see the emails used there: git -C "{repo}" log --format=%ae | sort | uniq -c'))
    if total == 0:
        checks.insert(0, Check(FAIL, "Work email", f"no commit by {who} in any local clone",
                               "check WORK_EMAIL: it must be the author email of your commits in the work repos"))
    else:
        checks.insert(0, Check(OK, "Work email", f"{total} commit(s) by {who} in {len(local)} local clone(s)" +
                               (f"; {len(urls)} URL entr{'y is' if len(urls) == 1 else 'ies are'} counted by the "
                                "next sync" if urls else "")))
    return checks


def check_mirror_remote(remote):
    ok, message = push.check_remote(remote)
    if ok:
        return Check(OK, "Mirror repo", f"{redact(remote)}: {message}")
    return Check(WARN, "Mirror repo", f"{redact(remote)}: {message}",
                 "check the URL; for ssh, load your key into ssh-agent (scheduled runs cannot type a passphrase), "
                 "or set PERSONAL_SSH_KEY")


def check_mirror_folder(mirror, expected_remote):
    if not os.path.isdir(os.path.join(mirror, ".git")):
        return Check(OK, "Mirror folder", f"{mirror}: created by the first sync")
    origin = origin_url(mirror)
    if origin and repo_key(origin) != repo_key(expected_remote):
        return Check(FAIL, "Mirror folder", f"{mirror} pushes to {redact(origin)}, not to your mirror repo: sync "
                     "refuses to push", f'git -C "{mirror}" remote set-url origin {redact(expected_remote)}  (if that '
                     "folder really is your mirror), or set MIRROR_DIR to a new folder")
    try:
        if mirror_repo.is_marked(mirror):
            return Check(OK, "Mirror folder", f"{mirror}: your mirror")
        if mirror_repo.only_mirror_commits(mirror):
            return Check(OK, "Mirror folder", f"{mirror}: holds only mirror commits (marked as the mirror by the next "
                         "sync)")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as e:
        return Check(WARN, "Mirror folder", f"{mirror}: cannot be read: {e}")
    return Check(FAIL, "Mirror folder", f"{mirror} has commits this tool did not make, so sync refuses to write to it",
                 f'set MIRROR_DIR to a new folder, or if it is your mirror: git -C "{mirror}" config '
                 f"{mirror_repo.MARKER} true")


def _email_matches_noreply(email, login):
    """GitHub's private address (<id>+<login>@users.noreply.github.com, or <login>@...) belongs to that account."""
    local, _, domain = email.lower().partition("@")
    return domain == "users.noreply.github.com" and local.split("+")[-1] == login.lower()


def check_github(cfg, find_token=push.owner_token):
    """On GitHub: the personal email is verified on the account that owns the mirror, and the repo's settings let
    its commits count (default branch main; private repos need "Private contributions" in the profile)."""
    found = find_token(cfg)
    if found is None:
        provider = provider_for(real_host(cfg.expected_remote), use_probe=False)
        if provider.name != "github":
            return [Check(SKIP, "Personal account", f"the mirror is on {provider.label}: the email and repo checks are "
                          "for GitHub only")]
        owner = repo_path(cfg.expected_remote).split("/")[0]
        return [Check(SKIP, "Personal account", f"no login of '{owner}' found on this computer, so your email and "
                      "the repo settings cannot be checked", "gh auth login (as your personal account)")]
    provider, host, owner, tok = found
    if provider.name != "github":
        return [Check(SKIP, "Personal account", f"the mirror is on {provider.label}: the email and repo checks are for "
                      "GitHub only")]
    api = Api(provider.api_url(host), provider.headers(tok))
    out = []
    path = repo_path(cfg.expected_remote)
    try:
        repo = api.get(f"/repos/{path}")
    except ApiError as e:
        out.append(Check(FAIL, "Mirror repo settings", f"{path}: not found, or '{owner}' cannot see it "
                         f"(HTTP {e.status})", "create it as an empty private repo in your personal account"))
    except (OSError, ValueError) as e:
        out.append(Check(SKIP, "Mirror repo settings", f"could not ask GitHub: {redact(e)}"))
    else:
        branch = repo.get("default_branch") or "main"
        if branch != "main":
            out.append(Check(FAIL, "Mirror repo settings", f"default branch is '{branch}', but the mirror is pushed to "
                             "'main': GitHub only counts commits on the default branch",
                             "set the default branch to main (repo Settings > General)"))
        elif repo.get("private"):
            out.append(Check(OK, "Mirror repo settings", f"{path}: private, default branch main. Your graph shows it "
                             "only with 'Include private contributions on my profile' switched on (profile settings)"))
        else:
            out.append(Check(OK, "Mirror repo settings", f"{path}: public, default branch main"))
    email = cfg.personal_email.strip()
    login = owner
    try:
        emails = api.get("/user/emails") or []
    except ApiError as e:
        if e.status in (401, 403, 404):
            out.append(Check(SKIP, "Personal email", f"the login of '{owner}' ({tok.source}) may not read your email "
                             "addresses, so whether it is verified cannot be checked",
                             "gh auth refresh -h github.com -s user:email   (then check again)"))
            return out
        raise
    except (OSError, ValueError) as e:
        out.append(Check(SKIP, "Personal email", f"could not ask GitHub: {redact(e)}"))
        return out
    entry = next((e for e in emails if str(e.get("email", "")).lower() == email.lower()), None)
    if entry and entry.get("verified"):
        out.append(Check(OK, "Personal email", f"{email} is verified on '{login}'"))
    elif _email_matches_noreply(email, login):
        out.append(Check(OK, "Personal email", f"{email} is the private address of '{login}'"))
    elif entry:
        out.append(Check(FAIL, "Personal email", f"{email} is on '{login}' but NOT verified: GitHub does not count the "
                         "mirror commits", "verify it: GitHub > Settings > Emails"))
    else:
        out.append(Check(FAIL, "Personal email", f"{email} is not an email of '{login}': GitHub does not count the "
                         "mirror commits",
                         "add and verify it (GitHub > Settings > Emails), or set PERSONAL_EMAIL to one that is"))
    return out


def check_logins(cfg, sources):
    """The login per work host for pull requests and reviews (the same choice a sync makes)."""
    if not cfg.pr_reviews:
        return [Check(SKIP, "Logins", "pull requests and reviews are off (commits only): no login needed")]
    try:
        r = accounts.report(cfg, sources=sources)
    except Exception as e:  # noqa: BLE001 - a check never crashes the report
        return [Check(WARN, "Logins", f"could not look them up: {redact(e)}")]
    if not r["hosts"]:
        return [Check(SKIP, "Logins", "no project has a remote URL, so there is no host for pull requests")]
    out = []
    for h in r["hosts"]:
        if not h["api"]:
            out.append(Check(SKIP, "Login", f"{h['scope']}: commits only ({h['provider']})"))
        elif h["login"]:
            out.append(Check(OK, "Login", f"{h['scope']}: {h['login']} via {h['source']}"))
        else:
            why = f"no usable login ({h['error']})" if h["error"] else "no login found that can see your projects"
            out.append(Check(WARN, "Login", f"{h['scope']}: {why}: its pull requests and reviews are skipped",
                             "log in with the host's CLI (gh auth login, glab auth login), or set WORK_TOKEN; "
                             "see docs/authentication.md"))
    return out


def check_schedule(state, logs, now=None):
    """Automatic runs: set up, and still working. `state` = schedule.current(), `logs` = report.read_logs()."""
    if not state.get("supported"):
        return Check(SKIP, "Automatic runs", f"not supported on '{state.get('platform')}'")
    if not (state.get("login") or state.get("daily")):
        return Check(OK, "Automatic runs", f"off: run `{command_name()} sync` yourself, or turn them on in Schedule")
    runs = [r for r in logs.get("runs", []) if r.get("mode") == "apply"]
    last_ok = runs[-1]["time"] if runs else None
    failed = next((e for e in logs.get("entries", []) if e.get("level") == "ERROR"
                   and e.get("message", "").startswith("scheduled sync failed")), None)
    if failed and (not last_ok or failed["time"] > last_ok):
        return Check(FAIL, "Automatic runs", f"the last scheduled run failed ({failed['time']}): {failed['message']}",
                     "see the Logs tab (or mirror.log), fix the cause, then sync once by hand")
    if not last_ok:
        return Check(WARN, "Automatic runs", "set up, but no sync has completed yet",
                     "run one sync by hand, or wait for the next scheduled run")
    now = now or datetime.now()
    try:
        age = now - datetime.strptime(last_ok, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        age = timedelta(0)
    if state.get("daily") and age > timedelta(days=STALE_DAYS):
        return Check(WARN, "Automatic runs", f"the last sync was {last_ok}, {age.days} days ago",
                     f"is the computer on at {state.get('time') or 'the daily time'}? Check the Logs tab")
    return Check(OK, "Automatic runs", f"last sync {last_ok}")


def check_log_files(log_dir):
    open_to_others = [p for p in (os.path.join(log_dir, "mirror.log"), state_path(log_dir)) if _private_problem(p)]
    if open_to_others:
        return Check(WARN, "Log files", f"{', '.join(open_to_others)} can be read by other users (the next run "
                     "fixes this)", "chmod 600 " + " ".join(f'"{p}"' for p in open_to_others))
    return Check(OK, "Log files", log_dir)


def run_checks(include_schedule=True):
    """Every check, in the order a reader fixes things. Stops after the settings if they cannot be read."""
    path = env_path()
    first, cfg = check_settings(path)
    checks = [first, check_git()]
    if cfg is None:
        return checks
    project_checks, local, urls = check_projects(cfg.repos_file or repos_path(), cfg.mirror_dir)
    checks += project_checks
    checks += check_work_emails(set(e.lower() for e in cfg.work_emails), local, urls)
    hosted = is_url(cfg.expected_remote) and bool(url_host(cfg.expected_remote))
    if is_url(cfg.expected_remote) or os.path.exists(os.path.expanduser(cfg.expected_remote)):
        checks.append(check_mirror_remote(cfg.expected_remote))
    else:
        checks.append(Check(FAIL, "Mirror repo", f"'{cfg.expected_remote}' is not a git URL",
                            "set EXPECTED_REMOTE to your personal repo, "
                            "e.g. git@github.com:you/work-contributions.git"))
    checks.append(check_mirror_folder(cfg.mirror_dir, cfg.expected_remote))
    if not hosted:
        checks.append(Check(SKIP, "Personal account", "the mirror repo is not on a git host"))
    else:
        try:
            checks += check_github(cfg)
        except Exception as e:  # noqa: BLE001 - a check never crashes the report
            checks.append(Check(SKIP, "Personal account", f"could not be checked: {redact(e)}"))
    checks += check_logins(cfg, (local, urls))
    log_dir = cfg.log_dir or LOG_DIR
    if include_schedule:
        from . import schedule
        checks.append(check_schedule(schedule.current(), read_logs(log_dir)))
    checks.append(check_log_files(log_dir))
    return checks


LABELS = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]", SKIP: "[skip]"}


def main(argv):
    p = argparse.ArgumentParser(prog=f"{command_name()} doctor", description=__doc__.split("\n\n")[0])
    p.add_argument("--json", action="store_true", help="machine-readable output (used by the setup page)")
    p.add_argument("--skip-schedule", action="store_true", help=argparse.SUPPRESS)   # the page checks it itself
    a = p.parse_args(argv)
    checks = run_checks(include_schedule=not a.skip_schedule)
    if a.json:
        print(json.dumps({"checks": [c._asdict() for c in checks]}))
    else:
        width = max(len(c.name) for c in checks)
        for c in checks:
            print(f"{LABELS[c.status]} {c.name:<{width}}  {c.detail}")
            if c.fix and c.status != OK:
                print(f"{'':>{len(LABELS[OK]) + width + 3}}fix: {c.fix}")
        failed = sum(c.status == FAIL for c in checks)
        warned = sum(c.status == WARN for c in checks)
        print("\n" + ("Everything checked is fine." if not (failed or warned) else
                      f"{failed} problem(s) to fix, {warned} warning(s)."))
    sys.stdout.flush()
    return 1 if any(c.status == FAIL for c in checks) else 0
