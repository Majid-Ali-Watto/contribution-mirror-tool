#!/usr/bin/env python3
"""Mirror contribution TIMESTAMPS (only) from local repos (and optionally PRs/reviews via the GitHub API)
into an empty-commit repo.

Reads from source repos: author date of commits matching --work-email. Nothing else
(no hashes, messages, paths, repo names) is read into the output or written anywhere.
Writes: empty commits with a generic message into --mirror. Never pushes.
Dry run by default; pass --apply to create commits.

Each run also appends one SUMMARY line to a LOCAL log file (logs/mirror.log): repos scanned and
new commits per repo (plus any warnings/errors). Repo paths appear only in that log, never in the mirror.
"""
import argparse, json, logging, logging.handlers, os, subprocess, sys
from collections import Counter
from datetime import datetime, timedelta, timezone

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
    out = git(repo, "log", "--all", "--format=%H %ae %aI", check=False)
    for line in out.splitlines():
        h, email, date = line.split(" ", 2)
        if h in seen:
            continue
        seen.add(h)
        if email.lower() in emails:
            dates.append(date)
    return dates

def gh_json(args, env):
    """Run `gh api ...` (paginated calls print one JSON document per page) and return all pages as a list."""
    out = subprocess.run(["gh", "api", *args], capture_output=True, text=True, env=env, check=True).stdout
    dec, i, pages = json.JSONDecoder(), 0, []
    while i < len(out):
        if out[i].isspace():
            i += 1
            continue
        obj, i = dec.raw_decode(out, i)
        pages.append(obj)
    return pages

def api_dates(gh_user):
    """Timestamps (only) of PRs you opened and PR reviews you submitted, last 365 days, from the work account.
    The contributions GraphQL API hides private-org items (only a total), so this uses search + REST.
    Returns (pr_dates, review_dates) as UTC strings ending in Z, exactly how git's %aI prints UTC, so re-runs dedupe."""
    token = subprocess.run(["gh", "auth", "token", "--user", gh_user],
                           capture_output=True, text=True, check=True).stdout.strip()
    env = {**os.environ, "GH_TOKEN": token}
    since = (datetime.now(timezone.utc) - timedelta(days=364)).strftime("%Y-%m-%d")
    login = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                           capture_output=True, text=True, env=env, check=True).stdout.strip()

    def search(q):
        items = []
        for page in gh_json(["-X", "GET", "search/issues", "-f", f"q={q}", "-f", "per_page=100", "--paginate"], env):
            items.extend(page["items"])
        return items

    prs = [i["created_at"] for i in search(f"author:{login} type:pr created:>={since}")]
    reviews = []
    for pr in search(f"reviewed-by:{login} type:pr -author:{login} updated:>={since}"):
        repo = pr["repository_url"].split("/repos/", 1)[1]
        for page in gh_json([f"repos/{repo}/pulls/{pr['number']}/reviews", "--paginate"], env):
            reviews.extend(r["submitted_at"] for r in page
                           if (r.get("user") or {}).get("login") == login and r.get("submitted_at")
                           and r["submitted_at"][:10] >= since)
    norm = lambda d: datetime.fromisoformat(d.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [norm(d) for d in prs], [norm(d) for d in reviews]

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
    p.add_argument("--gh-user", help="gh CLI account of your WORK GitHub login. If given, the timestamps of "
                   "PRs you opened and PR reviews you made (last 365 days) are mirrored too")
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

    if a.gh_user:
        try:
            prs, reviews = api_dates(a.gh_user)
            for label, dates in (("github-api:pull-requests", prs), ("github-api:reviews", reviews)):
                c = Counter(dates)
                per_repo.append((label, c))
                wanted.update(c)
        except Exception as e:  # keep mirroring commits even if the API is unreachable
            detail = (getattr(e, "stderr", "") or str(e)).strip().splitlines()[-1:] or [repr(e)]
            print(f"warning: PR/review fetch failed, skipped: {detail[0]}", file=sys.stderr)
            log.warning("PR/review fetch failed, skipped: %s", detail[0])

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
        by_repo.append(f"{os.path.basename(r).replace('github-api:', '')}={sum((c - covered).values())}")
        say(f"repo {r}: matching={sum(c.values())} already_mirrored={sum(covered.values())} "
            f"new={sum((c - covered).values())}")

    say(f"source repos scanned : {len(repos)}")
    say(f"matching contributions: {sum(wanted.values())}")
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
