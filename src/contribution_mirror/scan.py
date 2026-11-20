"""Reading commit dates from every listed project: fetch local clones, clone URL entries temporarily (or into an
opt-in cache), in parallel, and count each commit once even if it is in several of the listed repos."""
import hashlib
import os
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .fsutil import private_dir
from .gitutil import origin_url, rmtree, run_git, source_commits, token_env, work_env
from .logsetup import warn
from .providers import host_of
from .remotes import https_url, is_url, repo_key, scope_of

CLONE = ["clone", "--quiet", "--bare", "--filter=tree:0", "--no-tags"]
QUIET = ["-c", "gc.auto=0", "-c", "maintenance.auto=false"]


def _token_env(pick, base):
    return token_env(pick.provider.git_header(pick.token), host_of(pick.scope), base)


def fetch_repo(repo, creds):
    """Update remote-tracking refs of a work clone. Returns an error string, or None on success.
    No --prune and no merge/checkout: local branches, the working tree and uncommitted work are untouched.
    If the clone's own remote setup fails, retry over https with an automatically found token."""
    quiet = ["-C", repo, *QUIET]
    env = work_env(repo, batch_ssh=True)
    err = run_git(quiet + ["fetch", "--all", "--quiet"], env, 180)
    if err is None or err.startswith("timed out"):
        return err
    origin = origin_url(repo)
    pick = creds.pick(scope_of(origin)) if origin and is_url(origin) else None
    if not pick:
        return err
    err2 = run_git(quiet + ["fetch", "--quiet", https_url(origin), "+refs/heads/*:refs/remotes/origin/*"],
                   _token_env(pick, env), 180)
    return None if err2 is None else f"{err} (https fallback via {pick.source} also failed: {err2})"


def _clone(url, target, creds):
    """Bare, tree-less clone (commits only) of `url` into `target`; the fallback is https + a found token.
    Returns (env that worked, error or None)."""
    env = work_env(batch_ssh=True)
    err = run_git(CLONE + [url, target], env, 300)
    if err and not err.startswith("timed out"):
        pick = creds.pick(scope_of(url))
        if pick:
            if os.path.exists(target):
                rmtree(target)
            env = _token_env(pick, env)
            err2 = run_git(CLONE + [https_url(url), target], env, 300)
            err = None if err2 is None else f"{err} (https fallback via {pick.source} also failed: {err2})"
    return env, err


def _refresh(path, url, creds):
    """Fetch new commits into a cached clone (the partial-clone filter is remembered by the clone). Returns an error
    or None. The fallback briefly points the clone at the https URL (which never holds a token) and back."""
    env = work_env(batch_ssh=True)
    fetch = ["-C", path, *QUIET, "fetch", "--quiet", "--prune", "origin"]
    err = run_git(fetch, env, 300)
    if err and not err.startswith("timed out"):
        pick = creds.pick(scope_of(url))
        if pick:
            run_git(["-C", path, "remote", "set-url", "origin", https_url(url)], env, 30)
            try:
                err2 = run_git(fetch, _token_env(pick, env), 300)
            finally:
                run_git(["-C", path, "remote", "set-url", "origin", url], env, 30)
            err = None if err2 is None else f"{err} (https fallback via {pick.source} also failed: {err2})"
    return err


def cache_path(cache_dir, url):
    """Folder of a URL's cached clone: named by a hash, so no repo name appears in the folder name."""
    return os.path.join(cache_dir, hashlib.sha256(repo_key(url).encode()).hexdigest()[:20] + ".git")


def clear_tmp(tmp_root):
    """Empty the folder of temporary clones (owner-only). Called at the start of a run, under the run lock: a run that
    was killed (power loss, Windows' Cancel, SIGKILL) cannot clean up after itself, so the next one does."""
    if os.path.isdir(tmp_root):
        rmtree(tmp_root)
    private_dir(tmp_root)


def remote_dates(url, emails, creds, cache_dir=None, tmp_root=None):
    """Scan a repo that is not cloned locally. Returns ({hash: date}, error-or-None).

    Default: bare, tree-less clone (commits only) into a temp folder (in `tmp_root`) that is deleted afterwards. With a
    `cache_dir` the clone is kept there and only new commits are fetched on later runs (much faster for big repos,
    and for hosts that ignore the tree-less filter and send everything); if that fetch fails, the cached copy is
    still scanned."""
    if cache_dir:
        path = cache_path(cache_dir, url)
        if os.path.isdir(path):
            err = _refresh(path, url, creds)
            env = work_env()
            note = f"fetch failed, scanned the cached copy: {err}" if err else None
        else:
            os.makedirs(cache_dir, exist_ok=True)
            tmp = path + ".partial"
            if os.path.exists(tmp):
                rmtree(tmp)
            env, err = _clone(url, tmp, creds)
            if err:
                if os.path.exists(tmp):
                    rmtree(tmp)
                return {}, f"clone failed, skipped: {err}"
            run_git(["-C", tmp, "config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*"], env, 30)
            os.replace(tmp, path)
            note = None
        try:
            return source_commits(path, emails, env), note
        except RuntimeError as e:
            return {}, f"could not read commits, skipped: {e}"
    tmp = tempfile.mkdtemp(prefix="mirror-scan-", dir=tmp_root)
    try:
        env, err = _clone(url, tmp + "/r", creds)
        if err:
            return {}, f"temporary clone failed, skipped: {err}"
        try:
            return source_commits(tmp + "/r", emails, env), None
        except RuntimeError as e:
            return {}, f"could not read commits, skipped: {e}"
    finally:
        rmtree(tmp)


def count_once(per_repo):
    """[(label, {hash: date})] -> [(label, Counter of dates)], each commit counted only for the first repo that has
    it: a fork, or the same project listed twice (two clones, or a clone and a URL), must not double the count. The
    hashes are only used here, in memory."""
    seen, out = set(), []
    for label, commits in per_repo:
        c = Counter()
        for sha, date in commits.items():
            if sha not in seen:
                seen.add(sha)
                c[date] += 1
        out.append((label, c))
    return out


def scan_all(repos, remotes, emails, creds, no_fetch=False, cache_dir=None, tmp_root=None):
    """Commit dates of every project, in parallel. Returns [(label, Counter)] in a stable order: local repos
    first (label = path), then URL entries (label = 'remote:<name>'). Problems are warnings, never crashes."""
    def scan_local(r):
        err = None if no_fetch else fetch_repo(r, creds)
        if err:
            warn(f"fetch failed for {r}, scanning what is already local: {err}")
        try:
            return source_commits(r, emails)
        except RuntimeError as e:
            warn(f"could not read commits of {r}, skipped: {e}")
            return {}

    def scan_remote(url):
        commits, err = remote_dates(url, emails, creds, cache_dir, tmp_root)
        if err:
            warn(f"{url}: {err}")
        return commits

    with ThreadPoolExecutor(max(1, min(8, len(repos) + len(remotes)))) as ex:
        jobs = [(r, ex.submit(scan_local, r)) for r in repos]
        jobs += [("remote:" + repo_key(u).rsplit("/", 1)[-1], ex.submit(scan_remote, u)) for u in remotes]
        return count_once([(label, f.result()) for label, f in jobs])
