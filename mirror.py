#!/usr/bin/env python3
"""Mirror contribution TIMESTAMPS (only) from local repos (and optionally PRs/reviews via the GitHub API)
into an empty-commit repo.

Reads from source repos: author date of commits matching --work-email. Nothing else
(no hashes, messages, paths, repo names) is read into the output or written anywhere.
Writes: empty commits with a generic message into --mirror. Never pushes.
Dry run by default; pass --apply to create commits.

Each run also appends one SUMMARY line to a LOCAL log file (logs/mirror.log): repos scanned and
new commits per repo (plus any warnings/errors). Repo paths appear only in that log, never in the mirror.

Speed/memory notes:
  * source commits are streamed from `git log` line by line (nothing is buffered, no hash set: git log
    already visits each commit once, even when it is reachable from several branches);
  * PRs/reviews use the GitHub REST API directly over keep-alive HTTPS with a small thread pool;
  * after a successful --apply the start time is stored in logs/api-state.json, so the next run only asks
    GitHub about the last few days (with a safety margin). A full 365-day scan is used on the first run,
    when the mirror is empty, when the work account changes, or with --full-api;
  * new commits are written with a single `git fast-import` instead of one `git commit` per timestamp.
"""
import argparse, http.client, json, logging, logging.handlers, math, os, subprocess, sys, threading, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

MSG = "mirror"
# Parent of the folder this script lives in -> its children are the "sibling folders".
SIBLINGS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repos.json")
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_MAX_BYTES = 5 * 1024 * 1024   # rotate to a new file at 5 MB
LOG_BACKUPS = 10                  # keep mirror.log + 10 older files (~55 MB worst case)
API_WINDOW_DAYS = 364             # GitHub's graph covers about one year
API_MARGIN_DAYS = 3               # incremental runs re-check this much before the last successful run
API_WORKERS = 8                   # parallel review lookups
SEARCH_CAP = 1000                 # GitHub search returns at most 1000 results per query
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

def git_lines(repo, *args):
    """Yield stdout lines of a git command as they are produced (constant memory)."""
    p = subprocess.Popen(["git", "-C", repo, *args], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    try:
        yield from (line.rstrip("\n") for line in p.stdout)
    finally:
        p.stdout.close()
        p.wait()

def source_dates(repo, emails):
    """Author dates of matching commits. `git log --all` lists every commit once, so no dedupe is needed."""
    c = Counter()
    for line in git_lines(repo, "log", "--all", "--format=%ae %aI"):
        email, date = line.split(" ", 1)
        if email.lower() in emails:
            c[date] += 1
    return c

# ---------------------------------------------------------------- GitHub API (PRs and reviews) ----

class Api:
    """Tiny GitHub REST client: one keep-alive connection per thread, retries on rate limits/5xx."""
    def __init__(self, token):
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "contribution-mirror"}
        self.local = threading.local()

    def get(self, path, **params):
        url = path + ("?" + urlencode(params) if params else "")
        for attempt in range(6):
            conn = getattr(self.local, "conn", None)
            try:
                if conn is None:
                    conn = self.local.conn = http.client.HTTPSConnection("api.github.com", timeout=30)
                conn.request("GET", url, headers=self.headers)
                r = conn.getresponse()
                body = r.read()
            except (OSError, http.client.HTTPException):
                self.local.conn = None          # reconnect on the next attempt
                time.sleep(min(2 ** attempt, 20))
                continue
            if r.status == 200:
                return json.loads(body)
            if r.status in (403, 429) or r.status >= 500:
                wait = r.getheader("Retry-After")
                reset = r.getheader("X-RateLimit-Reset")
                if wait is None and r.getheader("X-RateLimit-Remaining") == "0" and reset:
                    wait = max(0, int(reset) - time.time()) + 1
                time.sleep(min(float(wait) if wait else 2 ** attempt, 90))
                continue
            raise RuntimeError(f"GitHub API {r.status} for {path}: {body[:200]!r}")
        raise RuntimeError(f"GitHub API: giving up on {path} after retries")

def search_items(api, base_q, field, a, b):
    """All PRs matching `base_q` with `field` (created/updated) in the inclusive date range [a, b].
    GitHub search stops at 1000 results, so a range that would overflow is split in two (nothing is dropped)."""
    q = f"{base_q} {field}:{a.isoformat()}..{b.isoformat()}"
    first = api.get("/search/issues", q=q, per_page=100, page=1)
    total = first["total_count"]
    if total > SEARCH_CAP and a < b:
        mid = a + (b - a) // 2
        return search_items(api, base_q, field, a, mid) + search_items(api, base_q, field, mid + timedelta(days=1), b)
    if total > SEARCH_CAP:
        log.warning("search for one day has more than %d results: some may be missing", SEARCH_CAP)
        print(f"warning: more than {SEARCH_CAP} results on {a}: some may be missing", file=sys.stderr)
    items = first["items"]
    for page in range(2, math.ceil(min(total, SEARCH_CAP) / 100) + 1):
        items += api.get("/search/issues", q=q, per_page=100, page=page)["items"]
    return items

def pr_reviews(api, repo, number, login, since):
    """Submission times of `login`'s reviews on one PR (all pages)."""
    out, page = [], 1
    while True:
        batch = api.get(f"/repos/{repo}/pulls/{number}/reviews", per_page=100, page=page)
        out += [r["submitted_at"] for r in batch
                if (r.get("user") or {}).get("login") == login and r.get("submitted_at")
                and r["submitted_at"][:10] >= since.isoformat()]
        if len(batch) < 100:
            return out
        page += 1

def api_dates(gh_user, since):
    """Timestamps (only) of PRs you opened and PR reviews you submitted since date `since` (UTC), from the work
    account. The contributions GraphQL API hides private-org items (only a total), so search + REST is used.
    Returns (pr_dates, review_dates) as UTC strings ending in Z, exactly how git's %aI prints UTC, so
    re-runs dedupe against the mirror."""
    token = subprocess.run(["gh", "auth", "token", "--user", gh_user],
                           capture_output=True, text=True, check=True).stdout.strip()
    api = Api(token)
    login = api.get("/user")["login"]
    end = datetime.now(timezone.utc).date() + timedelta(days=1)   # +1: tolerate clock/timezone skew
    with ThreadPoolExecutor(2) as ex:
        f_prs = ex.submit(search_items, api, f"author:{login} type:pr", "created", since, end)
        f_rev = ex.submit(search_items, api, f"reviewed-by:{login} type:pr -author:{login}", "updated", since, end)
        prs, reviewed = f_prs.result(), f_rev.result()
    pr_times = [i["created_at"] for i in prs]
    jobs = [(i["repository_url"].split("/repos/", 1)[1], i["number"]) for i in reviewed]
    del prs, reviewed
    with ThreadPoolExecutor(API_WORKERS) as ex:
        review_times = [t for ts in ex.map(lambda j: pr_reviews(api, j[0], j[1], login, since), jobs) for t in ts]
    norm = lambda d: datetime.fromisoformat(d.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [norm(d) for d in pr_times], [norm(d) for d in review_times]

def state_path(log_dir):
    return os.path.join(log_dir, "api-state.json")

def api_since(log_dir, gh_user, mirror_has_commits, full):
    """Start date for the API scan, plus a description for the log. Falls back to the full window whenever
    the saved state cannot be trusted, so nothing is skipped."""
    today = datetime.now(timezone.utc).date()
    full_start = today - timedelta(days=API_WINDOW_DAYS)
    if full:
        return full_start, "full (--full-api)"
    if not mirror_has_commits:
        return full_start, "full (mirror is empty)"
    try:
        with open(state_path(log_dir)) as f:
            st = json.load(f)
        if st.get("gh_user") != gh_user:
            return full_start, "full (work account changed)"
        last = datetime.fromisoformat(st["last_success"]).date()
        return max(full_start, last - timedelta(days=API_MARGIN_DAYS)), f"incremental (since {last} minus {API_MARGIN_DAYS}d)"
    except (OSError, ValueError, KeyError):
        return full_start, "full (no saved state)"

def save_state(log_dir, gh_user, started):
    tmp = state_path(log_dir) + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"gh_user": gh_user, "last_success": started.strftime("%Y-%m-%dT%H:%M:%S+00:00")}, f)
    os.replace(tmp, state_path(log_dir))

# ------------------------------------------------------------------------------ repo selection ----

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

# ----------------------------------------------------------------------------- writing commits ----

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
    p = subprocess.Popen(["git", "-C", mirror, "fast-import", "--quiet", "--date-format=raw"],
                         stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    ident = f"{name} <{email}>"
    msg = MSG.encode()
    first = has_main
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
    if p.wait() != 0:
        raise RuntimeError(f"git fast-import failed: {err.strip()}")

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
    p.add_argument("--full-api", action="store_true",
                   help="re-scan the whole 365-day window for PRs/reviews instead of only the recent days")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--log-dir", default=LOG_DIR,
                   help="folder for mirror.log (rotates at 5 MB, keeps 10 old files) and api-state.json; default: logs/ next to this script")
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
        c = source_dates(r, emails)
        per_repo.append((r, c))
        wanted.update(c)

    existing = Counter()
    if os.path.isdir(os.path.join(mirror, ".git")):
        for d in git_lines(mirror, "log", "--format=%aI"):
            existing[d] += 1

    api_ok, api_started = False, datetime.now(timezone.utc)
    if a.gh_user:
        since, how = api_since(a.log_dir, a.gh_user, bool(existing), a.full_api)
        try:
            prs, reviews = api_dates(a.gh_user, since)
            api_ok = True
            say(f"PRs/reviews          : {how}")
            for label, dates in (("github-api:pull-requests", prs), ("github-api:reviews", reviews)):
                c = Counter(dates)
                per_repo.append((label, c))
                wanted.update(c)
        except Exception as e:  # keep mirroring commits even if the API is unreachable
            detail = (getattr(e, "stderr", "") or str(e)).strip().splitlines()[-1:] or [repr(e)]
            print(f"warning: PR/review fetch failed, skipped: {detail[0]}", file=sys.stderr)
            log.warning("PR/review fetch failed, skipped: %s", detail[0])

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
    if todo:
        write_commits(mirror, list(todo.elements()), a.name, a.email)
    if api_ok:  # only after the commits are safely written
        save_state(a.log_dir, a.gh_user, api_started)
    say(f"created {sum(todo.values())} commits in {mirror}")
    summary("created")
    print("Review with `git log`, then add YOUR PERSONAL remote and push yourself.")

if __name__ == "__main__":
    main()
