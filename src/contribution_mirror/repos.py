"""Which projects to scan: the repos file (paths and URLs), folder scanning, and grouping by host."""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from .gitutil import origin_url
from .logsetup import log, warn
from .remotes import is_url, repo_key, repo_path, scope_of
from .settings import SIBLINGS


class ReposFileError(ValueError):
    """config/repos.json cannot be used (bad JSON, or not {"repos": [...]})."""


def origin_urls(repos):
    """{repo: its origin URL ('' if none)}, looked up in parallel (one git process each)."""
    if not repos:
        return {}
    with ThreadPoolExecutor(min(8, len(repos))) as ex:
        return dict(zip(repos, ex.map(origin_url, repos)))


def load_repos_file(path):
    """Projects from a JSON file: {"repos": [...]}. Each entry is a local clone path OR a git URL (ssh or https;
    the older "remote_repos" list of URLs is still accepted, with a note). No directory walking.
    Returns (local repo paths, URLs to scan from a temporary clone)."""
    try:
        with open(path, encoding="utf-8-sig") as f:              # -sig: a BOM from Windows editors is fine too
            cfg = json.load(f)
    except ValueError as e:
        raise ReposFileError(f"{path} is not valid JSON: {e}") from None
    listed = cfg.get("repos") if isinstance(cfg, dict) else None
    extra = cfg.get("remote_repos", []) if isinstance(cfg, dict) else []
    if not isinstance(listed, list) or not isinstance(extra, list) \
            or not all(isinstance(x, str) for x in listed + extra):
        raise ReposFileError(f'{path} must look like {{"repos": ["/path/to/clone", "git@host:org/repo.git"]}}')
    if extra:
        print(f'note: {path} uses the older "remote_repos" key. It still works; URLs can simply go in "repos" '
              'together with paths (see config/repos.example.json).')
    repos, urls = [], []
    for r in listed:
        if is_url(r):
            urls.append(r.strip())
            continue
        r = os.path.abspath(os.path.expanduser(r))
        if not os.path.exists(os.path.join(r, ".git")):
            warn(f"skipping '{r}' (not a git repo)")
        elif r not in repos:
            repos.append(r)
    return repos, list(dict.fromkeys(urls + [u.strip() for u in extra]))

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

def select_sources(src, repos_file, mirror):
    """Local repo paths and URL entries to scan. Precedence: --src folders > the repos file > every sibling
    folder (only if the repos file does not exist). The mirror itself is never scanned."""
    remotes = []
    if src:
        repos = [os.path.abspath(r) for r in find_repos(src)]
    elif os.path.exists(repos_file):
        repos, remotes = load_repos_file(repos_file)
    else:
        repos = [os.path.abspath(r) for r in find_repos([SIBLINGS])]
    if any(mirror == r or mirror.startswith(r + os.sep) for r in repos):
        log.error("mirror must not be inside a source repo")
        sys.exit("mirror must not be inside a source repo")
    repos = [r for r in repos if r != mirror]
    if remotes:  # a URL that is already cloned locally and listed would be counted twice
        local_keys = {repo_key(u) for u in origin_urls(repos).values()}
        remotes = [u for u in remotes if repo_key(u) not in local_keys]
    return repos, remotes


def group_by_scope(repos, remotes, origins=None):
    """{scope: [owner/repo, ...]} of everything listed (scope = host, or host/organisation on Azure DevOps).
    Used to pick the account that has your commits on each host. `origins` = origin_urls(repos), if known."""
    by_scope = {}
    origins = origin_urls(repos) if origins is None else origins
    for u in [origins.get(r, "") for r in repos] + remotes:
        if u and is_url(u):
            by_scope.setdefault(scope_of(u), []).append(repo_path(u))
    return {s: list(dict.fromkeys(v)) for s, v in by_scope.items()}
