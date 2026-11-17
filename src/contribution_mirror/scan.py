"""Reading commit dates from every listed project: fetch local clones, clone URL entries temporarily, in parallel."""
import os
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .gitutil import origin_url, rmtree, run_git, source_dates, token_env, work_env
from .logsetup import warn
from .providers import host_of
from .remotes import https_url, is_url, repo_key, scope_of

def fetch_repo(repo, creds):
    """Update remote-tracking refs of a work clone. Returns an error string, or None on success.
    No --prune and no merge/checkout: local branches, the working tree and uncommitted work are untouched.
    If the clone's own remote setup fails, retry over https with an automatically found token."""
    quiet = ["-C", repo, "-c", "gc.auto=0", "-c", "maintenance.auto=false"]
    err = run_git(quiet + ["fetch", "--all", "--quiet"], work_env(), 180)
    if err is None or err.startswith("timed out"):
        return err
    origin = origin_url(repo)
    pick = creds.pick(scope_of(origin)) if origin and is_url(origin) else None
    if not pick:
        return err
    env = token_env(pick.provider.git_header(pick.token), host_of(pick.scope))
    err2 = run_git(quiet + ["fetch", "--quiet", https_url(origin), "+refs/heads/*:refs/remotes/origin/*"], env, 180)
    return None if err2 is None else f"{err} (https fallback via {pick.source} also failed: {err2})"

def remote_dates(url, emails, creds):
    """Scan a repo that is not cloned locally: bare, tree-less clone (commits only) into a temp folder that is
    always deleted afterwards; the first attempt uses the URL as given, the fallback is https + found token.
    Returns (Counter, error-or-None)."""
    tmp = tempfile.mkdtemp(prefix="mirror-scan-")
    base = ["clone", "--quiet", "--bare", "--filter=tree:0", "--no-tags"]
    used_env = work_env()
    try:
        err = run_git(base + [url, tmp + "/r"], used_env, 300)
        if err and not err.startswith("timed out"):
            pick = creds.pick(scope_of(url))
            if pick:
                rmtree(tmp + "/r") if os.path.exists(tmp + "/r") else None
                used_env = token_env(pick.provider.git_header(pick.token), host_of(pick.scope))
                err2 = run_git(base + [https_url(url), tmp + "/r"], used_env, 300)
                err = None if err2 is None else f"{err} (https fallback via {pick.source} also failed: {err2})"
        if err:
            return Counter(), err
        try:
            return source_dates(tmp + "/r", emails, used_env), None
        except RuntimeError as e:
            return Counter(), str(e)
    finally:
        rmtree(tmp)

def scan_all(repos, remotes, emails, creds, no_fetch=False):
    """Commit dates of every project, in parallel. Returns [(label, Counter)] in a stable order: local repos
    first (label = path), then URL entries (label = 'remote:<name>'). Problems are warnings, never crashes."""
    def scan_local(r):
        err = None if no_fetch else fetch_repo(r, creds)
        if err:
            warn(f"fetch failed for {r}, scanning what is already local: {err}")
        try:
            return source_dates(r, emails)
        except RuntimeError as e:
            warn(f"could not read commits of {r}, skipped: {e}")
            return Counter()

    def scan_remote(url):
        c, err = remote_dates(url, emails, creds)
        if err:
            warn(f"temporary clone of {url} failed, skipped: {err}")
        return c

    with ThreadPoolExecutor(max(1, min(8, len(repos) + len(remotes)))) as ex:
        jobs = [(r, ex.submit(scan_local, r)) for r in repos]
        jobs += [("remote:" + repo_key(u).rsplit("/", 1)[-1], ex.submit(scan_remote, u)) for u in remotes]
        return [(label, f.result()) for label, f in jobs]
